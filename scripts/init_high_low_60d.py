"""
One-time initialization: reads from local stock.db, computes full historical
60-day high/low stats, and writes to Turso high_low_60d table.

Usage:
  TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... python scripts/init_high_low_60d.py
"""

import os
import sqlite3
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

TURSO_URL = os.environ["TURSO_DATABASE_URL"].replace("libsql://", "https://")
TURSO_TOKEN = os.environ["TURSO_AUTH_TOKEN"]
LOCAL_DB = os.environ.get("LOCAL_DB_PATH", "stock.db")
DAYS = 60
START_DATE = "1990-01-01"

HEADERS = {
    "Authorization": f"Bearer {TURSO_TOKEN}",
    "Content-Type": "application/json",
}


def turso_pipeline(requests_list):
    payload = {"requests": requests_list + [{"type": "close"}]}
    r = requests.post(f"{TURSO_URL}/v2/pipeline", headers=HEADERS, json=payload, timeout=30)
    if not r.ok:
        print(f"Turso error {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    return r.json()


def turso_exec(sql):
    turso_pipeline([{"type": "execute", "stmt": {"sql": sql}}])


def make_arg(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return {"type": "null"}
    if isinstance(val, str):
        return {"type": "text", "value": val}
    if isinstance(val, (int,)):
        return {"type": "integer", "value": str(val)}
    return {"type": "float", "value": float(val)}


def create_table():
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
    print("Table ready")


def compute_from_local():
    print(f"Reading stock.db from {LOCAL_DB} ...")
    conn = sqlite3.connect(LOCAL_DB)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA cache_size=-131072;")
    conn.execute("PRAGMA temp_store=MEMORY;")

    df_all = pd.read_sql_query(
        f"SELECT date, stock_id, close FROM stock_daily"
        f" WHERE date >= date('{START_DATE}', '-{DAYS} days')"
        f" ORDER BY stock_id, date",
        conn
    )
    df_taiex = pd.read_sql_query(
        f"SELECT date, close as taiex_close FROM stock_daily"
        f" WHERE stock_id = 'TAIEX' AND date >= '{START_DATE}'"
        f" ORDER BY date",
        conn
    )
    conn.close()
    print(f"Loaded {len(df_all)} rows")

    df_all['date'] = pd.to_datetime(df_all['date'], errors='coerce')
    df_all['close'] = pd.to_numeric(df_all['close'], errors='coerce')
    df_all = df_all.dropna(subset=['date', 'close'])

    df_taiex['date'] = pd.to_datetime(df_taiex['date'])
    df_taiex['taiex_close'] = pd.to_numeric(df_taiex['taiex_close'], errors='coerce')

    df_total = df_all.groupby('date')['stock_id'].nunique().reset_index(name='num_traded_stocks')

    df_idx = df_all.set_index('date')
    df_idx['min_close'] = df_idx.groupby('stock_id')['close'].transform(
        lambda x: x.rolling(f'{DAYS}D', min_periods=1).min()
    )
    df_idx['max_close'] = df_idx.groupby('stock_id')['close'].transform(
        lambda x: x.rolling(f'{DAYS}D', min_periods=1).max()
    )
    df_stats = df_idx.reset_index()

    df_lows = (df_stats[df_stats['close'] == df_stats['min_close']]
               .groupby('date')['stock_id'].nunique().reset_index(name='num_lows'))
    df_highs = (df_stats[df_stats['close'] == df_stats['max_close']]
                .groupby('date')['stock_id'].nunique().reset_index(name='num_highs'))

    df = (df_total
          .merge(df_lows, on='date', how='left')
          .merge(df_highs, on='date', how='left')
          .merge(df_taiex, on='date', how='left'))
    df[['num_lows', 'num_highs']] = df[['num_lows', 'num_highs']].fillna(0).astype(int)
    df['low_ratio'] = (df['num_lows'] * 100.0 / df['num_traded_stocks']).round(2)
    df['high_ratio'] = (df['num_highs'] * 100.0 / df['num_traded_stocks']).round(2)
    df = df[df['date'] >= pd.Timestamp(START_DATE)].sort_values('date').reset_index(drop=True)
    print(f"Computed {len(df)} date rows")
    return df


def write_to_turso(df):
    batch_size = 200
    total = len(df)
    written = 0
    for start in range(0, total, batch_size):
        chunk = df.iloc[start:start + batch_size]
        stmts = []
        for _, row in chunk.iterrows():
            stmts.append({
                "type": "execute",
                "stmt": {
                    "sql": "INSERT OR REPLACE INTO high_low_60d VALUES (?,?,?,?,?,?,?)",
                    "args": [
                        make_arg(row['date'].strftime('%Y-%m-%d')),
                        make_arg(row.get('taiex_close')),
                        make_arg(int(row['num_lows'])),
                        make_arg(int(row['num_highs'])),
                        make_arg(int(row['num_traded_stocks'])),
                        make_arg(float(row['low_ratio'])),
                        make_arg(float(row['high_ratio'])),
                    ]
                }
            })
        turso_pipeline(stmts)
        written += len(chunk)
        print(f"  Written {written}/{total}")
    print("Done!")


if __name__ == "__main__":
    create_table()
    df = compute_from_local()
    write_to_turso(df)
