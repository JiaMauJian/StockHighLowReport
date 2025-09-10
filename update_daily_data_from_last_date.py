import sqlite3
from FinMind.data import DataLoader
from datetime import datetime, timedelta
from fetch_all_stock_daily_by_stock import split_dataframe


def update_daily_data_from_last_date(token: str, db_path: str = 'stock.db'):
    print("更新資料庫資料")

    # 初始化 API
    api = DataLoader()
    api.login_by_token(api_token=token)

    # 資料庫連線
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # SQLite 效能優化設定
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = 100000;")

    # 今天日期
    today = datetime.today().strftime("%Y-%m-%d")

    try:
        # 查詢 TAIEX 最後一筆日期作為基準
        cursor.execute("SELECT MAX(date) FROM stock_daily WHERE stock_id = 'TAIEX'")
        result = cursor.fetchone()
        if result and result[0]:
            last_date = result[0]
            print(f"資料庫最新日期：{last_date}")
        else:
            last_date = "2020-01-01"
            print(f"找不到 TAIEX 資料，從 {last_date} 開始")

        # 找出該日已存在於 DB 的股票代號
        cursor.execute("SELECT DISTINCT stock_id FROM stock_daily WHERE date = ?", (last_date,))
        db_stock_ids = set(row[0] for row in cursor.fetchall())

        if last_date < today:
            print("索引移除中...")
            cursor.execute('DROP INDEX IF EXISTS idx_stock_daily_stock_id_date;')
            conn.commit()
            print("索引移除完成")

            while last_date < today:
                # 下一天
                next_date = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

                # 抓取資料
                df_daily = api.taiwan_stock_daily(start_date=next_date)

                if df_daily.empty:
                    print(f"{next_date} 無資料")
                    last_date = next_date
                    continue

                # 欄位過濾與股票代號過濾
                df_daily = df_daily[["date", "stock_id", "close"]]
                df_daily = df_daily[df_daily["stock_id"].isin(db_stock_ids)]

                if df_daily.empty:
                    print(f"{next_date} 無資料（過濾後）")
                    last_date = next_date
                    continue

                # 批次寫入資料
                conn.execute("BEGIN")
                for chunk in split_dataframe(df_daily, 300):
                    chunk.to_sql(
                        'stock_daily',
                        conn,
                        if_exists='append',
                        index=False,
                        method='multi'
                    )
                conn.commit()
                print(f"{next_date} 已寫入 {len(df_daily)} 筆")

                # 更新日期
                last_date = df_daily["date"].max()

            print("建立索引中...")
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_daily_stock_id_date ON stock_daily(stock_id, date);')
            conn.commit()
            print("索引建立完成")
        else:
            print("資料已是最新")

    except Exception as e:
        print(f"錯誤發生：{e}")
        input("請按 Enter 鍵結束程式...")
    finally:
        conn.close()