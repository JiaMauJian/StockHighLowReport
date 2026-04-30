"""
驗證 Turso high_low_60d 與本地 stock.db 計算結果是否一致（近一年）
"""

import os
import sqlite3
from datetime import date
from dateutil.relativedelta import relativedelta

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

TURSO_URL = os.environ["TURSO_DATABASE_URL"].replace("libsql://", "https://")
TURSO_TOKEN = os.environ["TURSO_AUTH_TOKEN"]
LOCAL_DB = os.environ.get("LOCAL_DB_PATH", "stock.db")
DAYS = 60

START_DATE = (date.today() - relativedelta(years=1)).strftime("%Y-%m-%d")
HEADERS = {
    "Authorization": f"Bearer {TURSO_TOKEN}",
    "Content-Type": "application/json",
}


def fetch_turso() -> pd.DataFrame:
    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": (
                        "SELECT date, num_lows, num_highs, num_traded_stocks, low_ratio, high_ratio"
                        f" FROM high_low_60d WHERE date >= '{START_DATE}' ORDER BY date"
                    )
                },
            },
            {"type": "close"},
        ]
    }
    r = requests.post(f"{TURSO_URL}/v2/pipeline", headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()

    rows = r.json()["results"][0]["response"]["result"]["rows"]
    cols = [c["name"] for c in r.json()["results"][0]["response"]["result"]["cols"]]
    data = [[cell.get("value") for cell in row] for row in rows]
    df = pd.DataFrame(data, columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    for c in ["num_lows", "num_highs", "num_traded_stocks"]:
        df[c] = df[c].astype(int)
    for c in ["low_ratio", "high_ratio"]:
        df[c] = df[c].astype(float)
    return df.set_index("date")


def compute_local() -> pd.DataFrame:
    conn = sqlite3.connect(LOCAL_DB)
    conn.execute("PRAGMA journal_mode=WAL;")

    fetch_from = (
        pd.Timestamp(START_DATE) - pd.Timedelta(days=DAYS + 10)
    ).strftime("%Y-%m-%d")

    df_all = pd.read_sql_query(
        f"SELECT date, stock_id, close FROM stock_daily"
        f" WHERE date >= '{fetch_from}' ORDER BY stock_id, date",
        conn,
    )
    conn.close()

    df_all["date"] = pd.to_datetime(df_all["date"], errors="coerce")
    df_all["close"] = pd.to_numeric(df_all["close"], errors="coerce")
    df_all = df_all.dropna(subset=["date", "close"])

    df_total = (
        df_all.groupby("date")["stock_id"]
        .nunique()
        .reset_index(name="num_traded_stocks")
    )

    df_idx = df_all.set_index("date")
    df_idx["min_close"] = df_idx.groupby("stock_id")["close"].transform(
        lambda x: x.rolling(f"{DAYS}D", min_periods=1).min()
    )
    df_idx["max_close"] = df_idx.groupby("stock_id")["close"].transform(
        lambda x: x.rolling(f"{DAYS}D", min_periods=1).max()
    )
    df_stats = df_idx.reset_index()

    df_lows = (
        df_stats[df_stats["close"] == df_stats["min_close"]]
        .groupby("date")["stock_id"]
        .nunique()
        .reset_index(name="num_lows")
    )
    df_highs = (
        df_stats[df_stats["close"] == df_stats["max_close"]]
        .groupby("date")["stock_id"]
        .nunique()
        .reset_index(name="num_highs")
    )

    df = (
        df_total.merge(df_lows, on="date", how="left")
        .merge(df_highs, on="date", how="left")
    )
    df[["num_lows", "num_highs"]] = df[["num_lows", "num_highs"]].fillna(0).astype(int)
    df["low_ratio"] = (df["num_lows"] * 100.0 / df["num_traded_stocks"]).round(2)
    df["high_ratio"] = (df["num_highs"] * 100.0 / df["num_traded_stocks"]).round(2)
    df = df[df["date"] >= pd.Timestamp(START_DATE)].sort_values("date").reset_index(drop=True)
    return df.set_index("date")


def main():
    print(f"比較近一年資料（{START_DATE} 起）...")
    print("抓取 Turso 資料...")
    df_turso = fetch_turso()
    print(f"  Turso: {len(df_turso)} 筆")

    print("計算本地資料...")
    df_local = compute_local()
    print(f"  Local: {len(df_local)} 筆")

    # 只比較兩邊都有的日期
    common_dates = df_turso.index.intersection(df_local.index)
    missing_in_turso = df_local.index.difference(df_turso.index)
    missing_in_local = df_turso.index.difference(df_local.index)

    if len(missing_in_turso):
        print(f"\n⚠️  本地有但 Turso 缺少的日期（{len(missing_in_turso)} 筆）：")
        print("  ", list(missing_in_turso.strftime("%Y-%m-%d"))[:10])

    if len(missing_in_local):
        print(f"\n⚠️  Turso 有但本地缺少的日期（{len(missing_in_local)} 筆）：")
        print("  ", list(missing_in_local.strftime("%Y-%m-%d"))[:10])

    cols = ["num_lows", "num_highs", "num_traded_stocks", "low_ratio", "high_ratio"]
    t = df_turso.loc[common_dates, cols]
    l = df_local.loc[common_dates, cols]

    diff_mask = (t != l).any(axis=1)
    diff_count = diff_mask.sum()

    print(f"\n共同日期：{len(common_dates)} 筆，其中不一致：{diff_count} 筆")

    if diff_count == 0:
        print("✅ 資料完全一致！")
    else:
        print(f"\n❌ 前 10 筆差異：")
        diff_dates = common_dates[diff_mask][:10]
        for d in diff_dates:
            print(f"\n  {d.date()}")
            for c in cols:
                tv, lv = t.loc[d, c], l.loc[d, c]
                if tv != lv:
                    print(f"    {c}: Turso={tv}  Local={lv}")


if __name__ == "__main__":
    main()
