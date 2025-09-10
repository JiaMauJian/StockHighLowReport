import sqlite3
import pandas as pd
from openpyxl import load_workbook
import threading
import time
import sys


def spinning_cursor():
    while True:
        for cursor in '|/-\\':
            yield cursor


def run_high_low_report(file_path, days):
    print(f"跑{days}日新高新低資料 -> {file_path}")

    # 嘗試開啟檔案
    try:
        wb = load_workbook(file_path)
    except FileNotFoundError:
        print(f"錯誤：找不到檔案 '{file_path}'，請確認檔案是否存在。")
        input("請按 Enter 鍵結束程式...")
        sys.exit(1)
    except PermissionError:
        print(f"錯誤：檔案 '{file_path}' 可能已被其他程式開啟，請先關閉檔案再執行。")
        input("請按 Enter 鍵結束程式...")
        sys.exit(1)

    # 刪除並重建工作表
    if "大盤" in wb.sheetnames:
        del wb["大盤"]
    wb.create_sheet(title="大盤")
    sheetMarket = wb["大盤"]

    # 表頭設定
    headers = [
        "日期", "成交量", "開盤價", "最高價", "最低價", "收盤價",
        f"股價低於{days}日家數", f"股價高於{days}日家數", "家數",
        f"股價低於{days}日家數比例", f"股價高於{days}日家數比例"
    ]
    for col, header in enumerate(headers, start=1):
        sheetMarket.cell(row=1, column=col, value=header)

    wb.save(file_path)

    conn = sqlite3.connect("stock.db")
    spinner = spinning_cursor()
    stop_spinner = False

    def spin():
        while not stop_spinner:
            sys.stdout.write(next(spinner))
            sys.stdout.flush()
            time.sleep(0.1)
            sys.stdout.write('\b')

    try:
        # 先載入大盤資料
        t = threading.Thread(target=spin)
        t.start()
        query1 = """
        SELECT * FROM stock_daily
        WHERE date >= date('now', '-10 years') AND stock_id = 'TAIEX'
        ORDER BY date;
        """
        df = pd.read_sql_query(query1, conn)
        stop_spinner = True
        t.join()
        print("大盤資料載入完成")

        for i, row in df.iterrows():
            excel_row = i + 2
            sheetMarket[f"A{excel_row}"] = row["date"]
            sheetMarket[f"F{excel_row}"] = row["close"]

        wb.save(file_path)

        print("資料滾算中...")

        # 新高新低 SQL（days 是變數）
        query2 = f"""
        WITH total AS (
            SELECT date, COUNT(DISTINCT stock_id) AS num_traded_stocks
            FROM stock_daily
            GROUP BY date
        ),
        lows AS (
            SELECT a.date, COUNT(DISTINCT a.stock_id) AS num_lows
            FROM stock_daily a
            WHERE a.close = (
                SELECT MIN(b.close) FROM stock_daily b
                WHERE b.stock_id = a.stock_id
                  AND b.date BETWEEN date(a.date, '-{days - 1} days') AND a.date
            )
            GROUP BY a.date
        ),
        highs AS (
            SELECT a.date, COUNT(DISTINCT a.stock_id) AS num_highs
            FROM stock_daily a
            WHERE a.close = (
                SELECT MAX(b.close) FROM stock_daily b
                WHERE b.stock_id = a.stock_id
                  AND b.date BETWEEN date(a.date, '-{days - 1} days') AND a.date
            )
            GROUP BY a.date
        )
        SELECT 
            total.date,
            COALESCE(lows.num_lows, 0) AS num_lows,
            COALESCE(highs.num_highs, 0) AS num_highs,
            total.num_traded_stocks,
            ROUND(COALESCE(lows.num_lows, 0) * 100.0 / total.num_traded_stocks, 2) AS low_ratio,
            ROUND(COALESCE(highs.num_highs, 0) * 100.0 / total.num_traded_stocks, 2) AS high_ratio
        FROM total
        LEFT JOIN lows ON total.date = lows.date
        LEFT JOIN highs ON total.date = highs.date
        WHERE total.date >= date('now', '-10 years')
        ORDER BY total.date;
        """

        stop_spinner = False
        t = threading.Thread(target=spin)
        t.start()
        df = pd.read_sql_query(query2, conn)
        stop_spinner = True
        t.join()
        print(f"{days}日新高新低資料載入完成")

        for i, row in df.iterrows():
            excel_row = i + 2
            sheetMarket[f"G{excel_row}"] = row["num_lows"]
            sheetMarket[f"H{excel_row}"] = row["num_highs"]
            sheetMarket[f"I{excel_row}"] = row["num_traded_stocks"]
            sheetMarket[f"J{excel_row}"] = row["low_ratio"]
            sheetMarket[f"K{excel_row}"] = row["high_ratio"]

        wb.save(file_path)

    except Exception as e:
        stop_spinner = True
        print(f"發生錯誤：{e}")
        input("請按 Enter 鍵結束程式...")
    finally:
        conn.close()
