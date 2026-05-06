"""
查詢指定日期的 60 日新高／新低個股明細。

Usage:
  python scripts/query_detail.py 2026-05-05
  python scripts/query_detail.py          # 不帶參數預設今天
"""

import os
import re
import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

FINMIND_TOKEN   = os.environ["FINMIND_TOKEN"]
FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
DAYS            = 60
FETCH_WINDOW    = DAYS + 15   # 多抓一點確保滿 60 個交易日


def finmind_get(params):
    resp = requests.get(
        FINMIND_API_URL,
        headers={"Authorization": f"Bearer {FINMIND_TOKEN}"},
        params=params,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def fetch_trading_dates():
    raw = finmind_get({"dataset": "TaiwanStockTradingDate"})
    return sorted(row["date"] for row in raw)


def fetch_valid_stock_ids():
    raw = finmind_get({"dataset": "TaiwanStockInfo"})
    if not raw:
        return None
    df = pd.DataFrame(raw)
    df = df[df["stock_id"].apply(lambda x: bool(re.fullmatch(r"[1-9]\d{3}", x)))]
    df = df[df["type"].isin(["twse", "tpex"])]
    df = df[df["industry_category"] != "存託憑證"]
    df = df.sort_values("date").drop_duplicates(subset=["stock_id"], keep="last")
    return dict(zip(df["stock_id"], df["stock_name"]))


def fetch_prices(trading_dates, query_date, valid_ids):
    """抓取 query_date 前 FETCH_WINDOW 個交易日（含當天）的收盤價。"""
    dates_up_to = [d for d in trading_dates if d <= query_date]
    dates_to_fetch = dates_up_to[-FETCH_WINDOW:]
    if not dates_to_fetch or dates_to_fetch[-1] != query_date:
        return pd.DataFrame()

    frames = []
    total = len(dates_to_fetch)
    for i, d in enumerate(dates_to_fetch, 1):
        print(f"\r  抓取 {d} ({i}/{total})", end="", flush=True)
        raw = finmind_get({"dataset": "TaiwanStockPrice", "start_date": d})
        if raw:
            df_day = pd.DataFrame(raw)[["date", "stock_id", "close"]]
            df_day = df_day[df_day["stock_id"].isin(valid_ids)]
            frames.append(df_day)
    print()

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["close"])


def compute_detail(df, query_date, stock_name):
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    target = pd.Timestamp(query_date)

    df = df.set_index("date").sort_index()
    df["min60"] = df.groupby("stock_id")["close"].transform(
        lambda x: x.rolling(f"{DAYS}D", min_periods=1).min()
    )
    df["max60"] = df.groupby("stock_id")["close"].transform(
        lambda x: x.rolling(f"{DAYS}D", min_periods=1).max()
    )
    df = df.reset_index()

    day = df[df["date"] == target].copy()
    if day.empty:
        return pd.DataFrame(), pd.DataFrame()

    highs = day[day["close"] == day["max60"]][["stock_id", "close", "max60"]].copy()
    lows  = day[day["close"] == day["min60"]][["stock_id", "close", "min60"]].copy()

    highs["stock_name"] = highs["stock_id"].map(stock_name)
    lows["stock_name"]  = lows["stock_id"].map(stock_name)

    highs = highs[["stock_id", "stock_name", "close"]].sort_values("stock_id").reset_index(drop=True)
    lows  = lows [["stock_id", "stock_name", "close"]].sort_values("stock_id").reset_index(drop=True)

    return highs, lows


def main():
    query_date = sys.argv[1] if len(sys.argv) > 1 else datetime.today().strftime("%Y-%m-%d")
    print(f"查詢日期：{query_date}")

    print("取得交易日清單...")
    trading_dates = fetch_trading_dates()
    if query_date not in trading_dates:
        print(f"❌ {query_date} 不是交易日")
        sys.exit(1)

    print("取得有效股票清單...")
    stock_name = fetch_valid_stock_ids()
    valid_ids  = set(stock_name.keys())
    print(f"  共 {len(valid_ids)} 檔一般股票")

    print(f"抓取近 {FETCH_WINDOW} 個交易日收盤價...")
    df = fetch_prices(trading_dates, query_date, valid_ids)
    if df.empty:
        print("❌ 無法取得資料")
        sys.exit(1)
    print(f"  共 {len(df)} 筆，{df['stock_id'].nunique()} 檔股票")

    highs, lows = compute_detail(df, query_date, stock_name)

    total = df[df["date"] == query_date]["stock_id"].nunique() if "date" in df.columns else "?"
    print(f"\n=== {query_date} 60日新高（{len(highs)} 檔 / 共 {total} 檔交易）===")
    print(highs.to_string(index=False))

    print(f"\n=== {query_date} 60日新低（{len(lows)} 檔 / 共 {total} 檔交易）===")
    print(lows.to_string(index=False))


if __name__ == "__main__":
    main()

# python scripts/query_detail.py 2026-05-05
