import os
import requests
import pandas as pd
from dotenv import load_dotenv
from FinMind.data import DataLoader

load_dotenv()

api = DataLoader()
api.login_by_token(api_token=os.environ["FINMIND_TOKEN"])

# ── 個股 ──────────────────────────────────────────────────────
df = api.taiwan_stock_daily(
    stock_id="2330",
    start_date="2024-01-01",
    end_date="2024-12-31",
)
print("=== 台積電 (2330) ===")
print(df.head())
print(f"共 {len(df)} 筆\n")

# ── 大盤指數 ──────────────────────────────────────────────────
df_taiex = api.taiwan_stock_daily(
    stock_id="TAIEX",
    start_date="2024-01-01",
    end_date="2024-12-31",
)
print("=== 加權指數 (TAIEX) ===")
print(df_taiex.head())
print(f"共 {len(df_taiex)} 筆\n")

# ── 上櫃指數 ──────────────────────────────────────────────────
df_tpex = api.taiwan_stock_daily(
    stock_id="TPEx",
    start_date="2024-01-01",
    end_date="2024-12-31",
)
print("=== 上櫃指數 (TPEx) ===")
print(df_tpex.head())
print(f"共 {len(df_tpex)} 筆")

# ── requests 直接呼叫 API ─────────────────────────────────────
token = os.environ["FINMIND_TOKEN"]
url = "https://api.finmindtrade.com/api/v4/data"
headers = {"Authorization": f"Bearer {token}"}

parameter = {
    "dataset": "TaiwanStockWeekPrice",
    "data_id": "6103",
    "start_date": "2023-05-15",
    "end_date": "2023-05-29",
}
resp = requests.get(url, headers=headers, params=parameter)
df_week = pd.DataFrame(resp.json()["data"])
print("=== 週K (6103) ===")
print(df_week.head())
