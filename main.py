# pyinstaller main.py --onefile

# 讀取 token.txt
with open("token.txt", "r", encoding="utf-8") as f:
    line = f.read().strip()
token = line.split("=", 1)[1].strip()

# 這個功能為了要把FinMind資料傳成資料庫，理論上只會用一次，之後這個功能就用不到了
# from fetch_all_stock_daily_by_stock import fetch_all_stock_daily
# fetch_all_stock_daily(token)

from fetch_missing_stock_data import fetch_missing_stock_data
fetch_missing_stock_data(token)

from update_daily_data_from_last_date import update_daily_data_from_last_date
update_daily_data_from_last_date(token)

from run_high_low_report import run_high_low_report
run_high_low_report("股價創20日新高新低比例.xlsx", 20)
run_high_low_report("股價創60日新高新低比例.xlsx", 60)
run_high_low_report("股價創240日新高新低比例.xlsx", 240)

print("結束 🎉")
input("請按 Enter 鍵結束程式...")
