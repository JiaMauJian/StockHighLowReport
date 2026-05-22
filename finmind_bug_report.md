# Bug Report: TaiwanStockMonthPrice — TAIEX 月K資料與日線彙整結果不一致

## 問題描述

`TaiwanStockMonthPrice` 提供的 TAIEX 月K資料，其 `open`、`max`、`min` 欄位與從 `TaiwanStockPrice`（日線）依標準邏輯彙整的月K結果存在明顯差異，且部分差異在邏輯上不合理。

## 月K標準彙整邏輯

| 欄位 | 定義 |
|------|------|
| open  | 該月第一個交易日的開盤價 |
| high  | 月內所有交易日最高價的最大值 |
| low   | 月內所有交易日最低價的最小值 |
| close | 該月最後一個交易日的收盤價 |

## 驗證方式

- 對象：TAIEX
- 日期範圍：2000-01-01 ~ 2026-05-11
- 比較：`TaiwanStockMonthPrice` vs `TaiwanStockPrice` 日線自行彙整

## 發現的問題

### 1. `close` — 完全一致 ✅

316 個月份差異全部為 0，無問題。

---

### 2. `open` — 差異嚴重 ❌

177 / 316 個月份有明顯差異，部分範例：

| ymonth  | API open  | Daily open | 差異      |
|---------|-----------|------------|-----------|
| 2026-04 | 33,218.02 | 31,892.33  | +1,325.69 |
| 2022-07 | 14,237.83 | 14,812.13  |  -574.30  |
| 2021-02 | 15,546.69 | 15,176.56  |  +370.13  |
| 2007-11 |  9,373.41 |  9,780.59  |  -407.18  |
| 2000-02 | 10,039.70 |  9,829.68  |  +210.02  |

---

### 3. `max` — 出現邏輯上不可能的數值 ❌

在 2007-11：

| 來源 | max 值 |
|------|--------|
| `TaiwanStockMonthPrice` | **9,437.43** |
| 日線彙整（取所有交易日最高的最大值） | **9,785.75** |

**`api_max < daily_max` 在邏輯上不可能成立**——月最高價必然 ≥ 月內任何一天的日線最高價。

這表示 `TaiwanStockMonthPrice` 對 TAIEX 的資料，可能並非從日線 OHLC 彙整而來，而是來自其他來源或計算方式不同。

## 重現程式碼

```python
import os
import requests
import pandas as pd
from datetime import date
from FinMind.data import DataLoader
from dotenv import load_dotenv

load_dotenv()
token = os.environ["FINMIND_TOKEN"]
API_BASE = "https://api.finmindtrade.com/api/v4/data"

# 月K API
resp = requests.get(API_BASE, params={
    "dataset": "TaiwanStockMonthPrice",
    "data_id": "TAIEX",
    "start_date": "2000-01-01",
    "end_date": date.today().strftime("%Y-%m-%d"),
    "token": token,
}, timeout=60)
month_api = pd.DataFrame(resp.json()["data"])
month_api["ym"] = month_api["ymonth"].str.replace(r"(\d{4})M(\d{2})", r"\1-\2", regex=True)
month_api = month_api[["ym", "open", "max", "min", "close"]].rename(
    columns={"open": "api_open", "max": "api_max", "min": "api_min", "close": "api_close"}
)

# 日線彙整
api = DataLoader()
api.login_by_token(api_token=token)
daily = api.taiwan_stock_daily(stock_id="TAIEX", start_date="2000-01-01",
                               end_date=date.today().strftime("%Y-%m-%d"))
daily["ym"] = daily["date"].str[:7]
month_daily = daily.groupby("ym").agg(
    daily_open=("open", "first"), daily_max=("max", "max"),
    daily_min=("min", "min"),    daily_close=("close", "last"),
).reset_index()

# 比較
df = pd.merge(month_api, month_daily, on="ym")
df["diff_max"] = (df["api_max"] - df["daily_max"]).abs()

# 不合理：api_max < daily_max
print(df[df["api_max"] < df["daily_max"]][["ym", "api_max", "daily_max", "diff_max"]])
```

## 期望行為

`TaiwanStockMonthPrice` 的 `open` / `max` / `min` 應與從 `TaiwanStockPrice` 日線依標準邏輯彙整的結果一致，或在文件中說明其計算方式與標準定義的差異。
