"""
驗證：TaiwanStockMonthPrice（月K API）vs 日線滾算月K
對象：TAIEX（大盤指數）
比較欄位：open / max / min / close
"""

import os
from datetime import date

import pandas as pd
import requests
from FinMind.data import DataLoader
from dotenv import load_dotenv

load_dotenv()

# ── 參數 ──────────────────────────────────────────────────────────────────────
STOCK_ID    = "TAIEX"
START_DATE  = "2000-01-01"          # TaiwanStockMonthPrice 最早只有 2000
END_DATE    = date.today().strftime("%Y-%m-%d")
DIFF_TOL    = 0.01                  # 允許誤差（絕對值），處理浮點問題
API_BASE    = "https://api.finmindtrade.com/api/v4/data"


# ── 連線 ──────────────────────────────────────────────────────────────────────
token = os.environ.get("FINMIND_TOKEN")
if not token:
    raise RuntimeError("找不到 FINMIND_TOKEN，請確認 .env 已設定")

api = DataLoader()
api.login_by_token(api_token=token)


# ── 1. 抓月K API 資料（直接 HTTP，DataLoader 未封裝此 dataset）──────────────
print("抓取 TaiwanStockMonthPrice ...")
resp = requests.get(API_BASE, params={
    "dataset":   "TaiwanStockMonthPrice",
    "data_id":   STOCK_ID,
    "start_date": START_DATE,
    "end_date":   END_DATE,
    "token":      token,
}, timeout=60)
resp.raise_for_status()
month_api = pd.DataFrame(resp.json()["data"])
print(f"   月K API 筆數：{len(month_api)}")
print(f"   欄位：{list(month_api.columns)}\n")

# ymonth 欄位格式為 "2020M04"，統一轉換成 "2020-04" 後當 key
if "ymonth" in month_api.columns:
    month_api["ym"] = month_api["ymonth"].str.replace(r"(\d{4})M(\d{2})", r"\1-\2", regex=True)
else:
    month_api["ym"] = month_api["date"].str[:7]

month_api = (
    month_api[["ym", "open", "max", "min", "close"]]
    .rename(columns={"open": "api_open", "max": "api_max",
                     "min": "api_min",  "close": "api_close"})
    .sort_values("ym")
    .reset_index(drop=True)
)


# ── 2. 抓日線再滾算月K ────────────────────────────────────────────────────────
print("📡 抓取日線資料（從 2000-01-01）...")
daily = api.taiwan_stock_daily(
    stock_id=STOCK_ID,
    start_date=START_DATE,
    end_date=END_DATE,
)
print(f"   日線筆數：{len(daily)}\n")

daily = daily.sort_values("date").reset_index(drop=True)
daily["ym"] = daily["date"].str[:7]

month_daily = (
    daily.groupby("ym", sort=True)
    .agg(
        daily_open  =("open",  "first"),
        daily_max   =("max",   "max"),
        daily_min   =("min",   "min"),
        daily_close =("close", "last"),
    )
    .reset_index()
)


# ── 3. 合併比較 ────────────────────────────────────────────────────────────────
df = pd.merge(month_api, month_daily, on="ym", how="inner")
print(f"✅ 對齊月份數：{len(df)}")
print(f"   月K API 範圍：{month_api['ym'].min()} ~ {month_api['ym'].max()}")
print(f"   日線滾算範圍：{month_daily['ym'].min()} ~ {month_daily['ym'].max()}\n")


# ── 4. 計算差異 ────────────────────────────────────────────────────────────────
for col in ["open", "max", "min", "close"]:
    df[f"diff_{col}"] = (df[f"api_{col}"] - df[f"daily_{col}"]).abs()

cols_diff = ["diff_open", "diff_max", "diff_min", "diff_close"]

# 4a. 統計摘要
print("=" * 60)
print("📊 差異統計（絕對值）")
print("=" * 60)
print(df[cols_diff].describe().to_string())
print()

# 4b. 有差異的月份
has_diff = df[(df[cols_diff] > DIFF_TOL).any(axis=1)].copy()
print(f"⚠️  差異 > {DIFF_TOL} 的月份數：{len(has_diff)} / {len(df)}")

if len(has_diff) > 0:
    display_cols = ["ym",
                    "api_open",  "daily_open",  "diff_open",
                    "api_max",   "daily_max",   "diff_max",
                    "api_min",   "daily_min",   "diff_min",
                    "api_close", "daily_close", "diff_close"]
    pd.set_option("display.max_rows", 60)
    pd.set_option("display.width", 200)
    print(has_diff[display_cols].to_string(index=False))
else:
    print("✅ 所有月份的 open / max / min / close 完全一致（誤差在容許範圍內）")

print()

# 4c. 各欄位最大差異月份
if len(df) > 0:
    print("=" * 60)
    print("各欄位最大差異月份")
    print("=" * 60)
    for col in ["open", "max", "min", "close"]:
        idx = df[f"diff_{col}"].idxmax()
        row = df.loc[idx]
        print(f"  {col:5s}  最大差異 {row[f'diff_{col}']:.4f}"
              f"  @ {row['ym']}"
              f"  API={row[f'api_{col}']:.2f}  Daily={row[f'daily_{col}']:.2f}")
