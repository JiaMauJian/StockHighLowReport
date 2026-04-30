"""
Daily GitHub Actions script:
  1. Fetch last 70 days from FinMind (enough for 60-day rolling window)
  2. Compute rolling 60-day high/low with pandas (same logic as run_high_low_report.py option 2)
  3. Upsert results into Turso high_low_60d (INSERT OR REPLACE — no full rewrite needed)

Does NOT read from Turso stock_daily — FinMind is the source for daily updates.
Initial full-history load is handled separately by init_high_low_60d.py.
"""

import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from FinMind.data import DataLoader
from dotenv import load_dotenv

load_dotenv()

TURSO_URL     = os.environ["TURSO_DATABASE_URL"].replace("libsql://", "https://")
TURSO_TOKEN   = os.environ["TURSO_AUTH_TOKEN"]
FINMIND_TOKEN = os.environ["FINMIND_TOKEN"]

DAYS         = 60
FETCH_WINDOW = DAYS + 10  # 70 days gives a full 60-day window for every computed date

HEADERS = {
    "Authorization": f"Bearer {TURSO_TOKEN}",
    "Content-Type": "application/json",
}


# ─── Turso helpers ────────────────────────────────────────────────────────────

def turso_pipeline(stmts, timeout_s=60):
    payload = {"requests": stmts + [{"type": "close"}]}
    r = requests.post(f"{TURSO_URL}/v2/pipeline", headers=HEADERS, json=payload, timeout=timeout_s)
    r.raise_for_status()
    return r.json()


def turso_exec(sql):
    turso_pipeline([{"type": "execute", "stmt": {"sql": sql}}])


def turso_scalar(sql):
    result = turso_pipeline([{"type": "execute", "stmt": {"sql": sql}}])
    rows = result["results"][0]["response"]["result"]["rows"]
    if rows and rows[0][0]["value"] is not None:
        return rows[0][0]["value"]
    return None


def make_arg(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return {"type": "null"}
    if isinstance(val, str):
        return {"type": "text", "value": val}
    if isinstance(val, int):
        return {"type": "integer", "value": str(val)}
    return {"type": "float", "value": float(val)}


# ─── Fetch from FinMind ───────────────────────────────────────────────────────

def fetch_recent_stock_data(api):
    """
    Fetches enough data to cover all missing days + the 60-day rolling window.
    If the last computed date was N days ago, fetches N + 60 + buffer days from FinMind.
    """
    today     = datetime.today()
    today_str = today.strftime("%Y-%m-%d")

    # How far back is our last computed row?
    last_computed = turso_scalar("SELECT MAX(date) FROM high_low_60d")
    if last_computed:
        days_missing = (today - datetime.strptime(last_computed, "%Y-%m-%d")).days
        print(f"Last computed date: {last_computed} ({days_missing} days ago)")
    else:
        days_missing = 0

    # Always fetch at least FETCH_WINDOW (70) days; extend if there are missed days
    fetch_days = max(FETCH_WINDOW, days_missing + DAYS + 10)
    fetch_from = (today - timedelta(days=fetch_days)).strftime("%Y-%m-%d")

    # 不需要比對新公司清單：taiwan_stock_daily 不指定 stock_id 時會回傳全市場所有股票，
    # 新上市公司自然包含在內。本地 stock.db 才需要比對，因為要補抓新公司的完整歷史資料。
    print(f"Fetching FinMind data from {fetch_from} to {today_str} ...")
    df = api.taiwan_stock_daily(start_date=fetch_from, end_date=today_str)
    print(f"FinMind returned {len(df)} rows, columns: {list(df.columns) if not df.empty else '[]'}")
    if df.empty:
        print("No data from FinMind")
        return pd.DataFrame()

    # Check OTC availability for today
    today_rows = df[df["date"] == today_str]
    if not today_rows.empty and "TPEx" not in today_rows["stock_id"].values:
        print(f"OTC data not ready for {today_str}; latest available date will be used")

    df = df[["date", "stock_id", "close"]].copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"])
    print(f"Fetched {len(df)} rows ({df['date'].nunique()} trading days, {df['stock_id'].nunique()} stocks)")
    return df


# ─── Compute 60-day rolling ───────────────────────────────────────────────────

def compute_60d(df_raw):
    """
    Identical logic to run_high_low_report.py.
    Returns a DataFrame indexed by date with high/low stats.
    Only returns rows that have a full 60-day window (date >= fetch_from + 60 days).
    """
    df = df_raw.copy()
    df["date"] = pd.to_datetime(df["date"])

    df_taiex = df[df["stock_id"] == "TAIEX"][["date", "close"]].rename(columns={"close": "taiex_close"})
    df_stocks = df.copy()

    df_total = df_stocks.groupby("date")["stock_id"].nunique().reset_index(name="num_traded_stocks")

    df_idx = df_stocks.set_index("date")
    df_idx["min_close"] = df_idx.groupby("stock_id")["close"].transform(
        lambda x: x.rolling(f"{DAYS}D", min_periods=1).min()
    )
    df_idx["max_close"] = df_idx.groupby("stock_id")["close"].transform(
        lambda x: x.rolling(f"{DAYS}D", min_periods=1).max()
    )
    df_stats = df_idx.reset_index()

    df_lows  = (df_stats[df_stats["close"] == df_stats["min_close"]]
                .groupby("date")["stock_id"].nunique().reset_index(name="num_lows"))
    df_highs = (df_stats[df_stats["close"] == df_stats["max_close"]]
                .groupby("date")["stock_id"].nunique().reset_index(name="num_highs"))

    result = (df_total
              .merge(df_lows,  on="date", how="left")
              .merge(df_highs, on="date", how="left")
              .merge(df_taiex, on="date", how="left"))
    result[["num_lows", "num_highs"]] = result[["num_lows", "num_highs"]].fillna(0).astype(int)
    result["low_ratio"]  = (result["num_lows"]  * 100.0 / result["num_traded_stocks"]).round(2)
    result["high_ratio"] = (result["num_highs"] * 100.0 / result["num_traded_stocks"]).round(2)

    # Only keep dates where we have a full 60-day window
    min_date = df_stocks["date"].min() + pd.Timedelta(days=DAYS)
    result = result[result["date"] >= min_date].sort_values("date").reset_index(drop=True)

    return result


# ─── Write to Turso ───────────────────────────────────────────────────────────

def ensure_table():
    turso_exec("""
        CREATE TABLE IF NOT EXISTS high_low_60d (
            date TEXT PRIMARY KEY,
            taiex_close REAL,
            num_lows INTEGER,
            num_highs INTEGER,
            num_traded_stocks INTEGER,
            low_ratio REAL,
            high_ratio REAL
        )
    """)


def upsert_results(df):
    stmts = []
    for _, row in df.iterrows():
        stmts.append({
            "type": "execute",
            "stmt": {
                "sql": "INSERT OR REPLACE INTO high_low_60d VALUES (?,?,?,?,?,?,?)",
                "args": [
                    make_arg(row["date"].strftime("%Y-%m-%d")),
                    make_arg(row.get("taiex_close")),
                    make_arg(int(row["num_lows"])),
                    make_arg(int(row["num_highs"])),
                    make_arg(int(row["num_traded_stocks"])),
                    make_arg(float(row["low_ratio"])),
                    make_arg(float(row["high_ratio"])),
                ],
            },
        })

    for i in range(0, len(stmts), 200):
        turso_pipeline(stmts[i : i + 200])

    print(f"Upserted {len(df)} rows into high_low_60d")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    api = DataLoader()
    api.login_by_token(api_token=FINMIND_TOKEN)

    ensure_table()

    df_raw = fetch_recent_stock_data(api)
    if df_raw.empty:
        print("Nothing to compute, exiting")
        exit(0)

    df_result = compute_60d(df_raw)
    print(f"Computed {len(df_result)} date rows")

    upsert_results(df_result)
