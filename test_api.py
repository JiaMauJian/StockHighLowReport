import os
from datetime import datetime
from dotenv import load_dotenv
from FinMind.data import DataLoader

load_dotenv()
token = os.environ.get("FINMIND_TOKEN")

api = DataLoader()
api.login_by_token(api_token=token)

today = datetime.today().strftime("%Y-%m-%d")
print(f"今天日期：{today}")

df_check = api.taiwan_stock_daily(start_date=today)
print(f"API 回傳總筆數：{len(df_check)}")
if not df_check.empty:
    print(f"日期範圍：{df_check['date'].min()} ~ {df_check['date'].max()}")
    print(f"含有的 stock_id 樣本：{df_check['stock_id'].unique()[:20]}")

today_check = df_check[df_check["date"] == today] if not df_check.empty else df_check
print(f"\n今天 ({today}) 的筆數：{len(today_check)}")
if not today_check.empty:
    print(f"'TPEx' 是否在今天資料中：{'TPEx' in today_check['stock_id'].values}")
    print(f"'TAIEX' 是否在今天資料中：{'TAIEX' in today_check['stock_id'].values}")
    print(f"'9962' 是否在今天資料中：{'9962' in today_check['stock_id'].values}")
    print(f"今天不重複股票數：{today_check['stock_id'].nunique()}")
else:
    print("今天無資料")
