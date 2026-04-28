import re
import sqlite3
from FinMind.data import DataLoader
from datetime import datetime, timedelta


def split_dataframe(df, chunk_size=90):
    for start in range(0, len(df), chunk_size):
        yield df.iloc[start:start + chunk_size]


def fetch_all_stock_daily(token, db_path='stock.db', default_start_date="1980-01-01"):
    # 登入 API
    api = DataLoader()
    api.login_by_token(api_token=token)

    # 資料庫連線
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 建立資料表（若不存在）
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS stock_daily (
        date TEXT,
        stock_id TEXT,
        close REAL
    )
    ''')

    # 設定 SQLite 效能參數
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = 100000;")

    # 抓取股票清單
    df = api.taiwan_stock_info()
    df = df[df["stock_id"].apply(lambda x: bool(re.fullmatch(r"[1-9]\d{3}", x)))]
    df = df[df["date"] == df["date"].max()]
    df = df.drop_duplicates(subset=["stock_id"])
    df = df[df["type"].isin(["twse", "tpex"])]
    df = df.sort_values(by="stock_id")
    stock_ids = df["stock_id"].tolist()
    stock_ids.append("TAIEX")

    try:
        # print("建立索引中...")
        # cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_daily_stock_id_date ON stock_daily(stock_id, date);')
        # conn.commit()
        # print("索引建立完成")

        # 一次查出所有 stock_id 的最新日期
        stock_id_dates = {}
        for stock_id in stock_ids:
            cursor.execute("SELECT MAX(date) FROM stock_daily WHERE stock_id = ?", (stock_id,))
            result = cursor.fetchone()
            if result and result[0]:
                last_date = (datetime.strptime(result[0], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                last_date = default_start_date
            stock_id_dates[stock_id] = last_date

        end_date = datetime.today().strftime("%Y-%m-%d")

        # 若已有索引，先移除（避免影響 insert 效能）
        # print("索引移除中...")
        # cursor.execute('DROP INDEX IF EXISTS idx_stock_daily_stock_id_date;')
        # conn.commit()
        # print("索引移除完成")

        # 開始逐檔處理
        for i, stock_id in enumerate(stock_ids):
            last_date = stock_id_dates[stock_id]

            if last_date >= end_date:
                print(f"[{i + 1}/{len(stock_ids)}] {stock_id} 資料已完整，略過")
                continue

            print(f"[{i + 1}/{len(stock_ids)}] 抓取 {stock_id} 資料中（從 {last_date}）...")

            try:
                df_daily = api.taiwan_stock_daily(
                    stock_id=stock_id,
                    start_date=last_date,
                    end_date=end_date
                )

                if df_daily.empty:
                    print(f"  {stock_id} 無資料，略過")
                    continue

                df_daily = df_daily[["date", "stock_id", "close"]]

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

                print(f"  已寫入 {len(df_daily)} 筆")

            except Exception as e:
                print(f"  錯誤發生於 {stock_id}：{e}")
                input("請按 Enter 鍵結束程式...")
                continue

        # 重建索引加快查詢速度
        # print("建立索引中...")
        # cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_daily_stock_id_date ON stock_daily(stock_id, date);')
        # conn.commit()
        # print("索引建立完成")

        print("所有股票日資料抓取完畢！")
        conn.close()

    except KeyboardInterrupt:
        print("\n手動中斷，程式結束。已抓取的資料保留，可下次接續")
        input("請按 Enter 鍵結束程式...")
        conn.close()

    finally:
        conn.close()
