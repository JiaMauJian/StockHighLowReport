# pyinstaller main.py --onefile

import os
from datetime import datetime


# =============================
# 通用前置作業：讀取 token 並更新資料
# =============================
def prepare_stock_data():
    """
    準備股票資料：
    1. 讀取 token.txt
    2. 更新每日資料
    3. 補齊缺漏資料
    """
    if not os.path.exists("token.txt"):
        print("❌ 找不到 token.txt，請確認檔案存在。")
        return None

    with open("token.txt", "r", encoding="utf-8") as f:
        line = f.read().strip()
    token = line.split("=", 1)[1].strip()

    print("🔄 正在更新每日資料...")
    from update_daily_data_from_last_date import update_daily_data_from_last_date
    update_daily_data_from_last_date(token)

    print("🔍 正在補齊缺漏資料...")
    from fetch_missing_stock_data import fetch_missing_stock_data
    fetch_missing_stock_data(token)

    print("✅ 資料準備完成。")
    return token


# =============================
# 功能一：計算新高新低報告
# =============================
def run_high_low_process():
    print("請選擇要產生的新高新低報告天數：")
    print("1. 20日")
    print("2. 60日")
    print("3. 240日")
    print("4. 60日-全部資料")

    choice = input("請輸入選項 : ").strip()

    today = datetime.today()
    try:
        ten_years_ago = today.replace(year=today.year - 10)
    except ValueError:
        ten_years_ago = today.replace(year=today.year - 10, day=28)
    start_date_10y = ten_years_ago.strftime("%Y-%m-%d")

    keep_vba = False
    if choice == "1":
        days = 20
        start_date = start_date_10y
        file_name = "股價創20日新高新低比例.xlsx"
    elif choice == "2":
        days = 60
        start_date = start_date_10y
        file_name = "股價創60日新高新低比例.xlsx"
    elif choice == "3":
        days = 240
        start_date = start_date_10y
        file_name = "股價創240日新高新低比例.xlsx"
    elif choice == "4":
        days = 60
        start_date = "1999-01-01"
        keep_vba = True
        file_name = "股價創60日新高新低比例_全部資料.xlsm"
    else:
        print("輸入錯誤，程式結束。")
        return

    print(f"正在產生 {days} 日新高新低報告...")

    from run_high_low_report import run_high_low_report
    run_high_low_report(file_name, days, start_date, keep_vba)

    print("✅ 新高新低報告產生完成 🎉")
    input("請按 Enter 鍵跳出...")


# =============================
# 主程式進入點
# =============================
def main():
    token = prepare_stock_data()
    if not token:
        return

    while True:
        print("\n請選擇要執行的功能：")
        print("1 產生新高新低報告")
        print("0 結束程式")
        choice = input("請輸入 1 或 0：").strip()

        if choice == "1":
            run_high_low_process()

        elif choice == "0":
            input("請按 Enter 鍵結束程式...")
            break

        else:
            print("❌ 輸入錯誤，請輸入 1 或 0。")


if __name__ == "__main__":
    main()
