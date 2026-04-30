# pyinstaller main.py --onefile

import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()


def prepare_stock_data():
    """
    準備股票資料：
    1. 讀取 .env 中的 FINMIND_TOKEN
    2. 更新每日資料
    3. 補齊缺漏資料
    """
    token = os.environ.get("FINMIND_TOKEN")
    if not token:
        print("❌ 找不到 FINMIND_TOKEN，請確認 .env 檔案已設定。")
        return None

    print("🔄 正在更新每日資料...")
    from update_daily_data_from_last_date import update_daily_data_from_last_date
    update_daily_data_from_last_date(token)

    print("🔍 正在補齊缺漏資料...")
    from fetch_missing_stock_data import fetch_missing_stock_data
    fetch_missing_stock_data(token)

    print("✅ 資料準備完成。")
    return token


def main():
    today = datetime.today()
    try:
        ten_years_ago = today.replace(year=today.year - 10)
    except ValueError:
        ten_years_ago = today.replace(year=today.year - 10, day=28)
    start_date_10y = ten_years_ago.strftime("%Y-%m-%d")

    options = {
        "1": (20,  start_date_10y,  False, "股價創20日新高新低比例.xlsx"),
        "2": (60,  start_date_10y,  False, "股價創60日新高新低比例.xlsx"),
        "3": (240, start_date_10y,  False, "股價創240日新高新低比例.xlsx"),
        "4": (60,  "1999-01-01",    True,  "股價創60日新高新低比例_全部資料.xlsm"),
    }

    while True:
        print("\n請選擇要產生的新高新低報告：")
        print("1. 20日")
        print("2. 60日")
        print("3. 240日")
        print("4. 60日-全部資料")
        print("5. 產生 Dashboard")
        print("0. 結束程式")
        choice = input("請輸入選項：").strip()

        if choice == "0":
            break

        if choice == "5":
            from run_dashboard import run_dashboard
            run_dashboard()
            input("請按 Enter 鍵繼續...")
            continue

        if choice not in options:
            print("❌ 輸入錯誤，請輸入 0-5。")
            continue

        token = prepare_stock_data()
        if not token:
            continue

        days, start_date, keep_vba, file_name = options[choice]
        print(f"正在產生 {days} 日新高新低報告...")

        from run_high_low_report import run_high_low_report
        run_high_low_report(file_name, days, start_date, keep_vba)

        print("✅ 新高新低報告產生完成 🎉")
        input("請按 Enter 鍵繼續...")


if __name__ == "__main__":
    main()
