import os
from dotenv import load_dotenv
from FinMind.data import DataLoader

load_dotenv()
token = os.environ["FINMIND_TOKEN"]

api = DataLoader()
api.login_by_token(api_token=token)

df = api.taiwan_stock_daily(start_date='2026-04-28')
result = df[df["stock_id"] == "TPEx"]
if result.empty:
    print("TPEx 不在今日資料中")
else:
    print(result)

# 查詢 1902 的股票基本資料
print("\n--- taiwan_stock_info 1902 ---")
df_info = api.taiwan_stock_info()
print(df_info[df_info["stock_id"] == "1902"])