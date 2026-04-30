import sqlite3
import re
from FinMind.data import DataLoader
from datetime import datetime, timedelta
from fetch_all_stock_daily_by_stock import split_dataframe


def fetch_missing_stock_data(token: str, db_path: str = 'stock.db'):
    print("檢查有無新公司資料")

    api = DataLoader()
    api.login_by_token(api_token=token)

    df = api.taiwan_stock_info()
    df = df[df["stock_id"].apply(lambda x: bool(re.fullmatch(r"[1-9]\d{3}", x)))]
    df = df[df["date"] == df["date"].max()]
    df = df.drop_duplicates(subset=["stock_id"])
    df = df[df["type"].isin(["twse", "tpex"])]
    df = df.sort_values(by="stock_id")
    stock_ids = df["stock_id"].tolist()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = 100000;")
    conn.execute("PRAGMA mmap_size = 536870912;")  # 512MB memory-mapped I/O
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT DISTINCT stock_id FROM stock_daily")
        db_stock_ids = set(row[0] for row in cursor.fetchall())
        missing_stock_ids = set(stock_ids) - db_stock_ids

        print(f"資料庫中缺少 {len(missing_stock_ids)} 檔股票")
        stock_info = df.set_index("stock_id")[["stock_name", "type"]].to_dict("index")
        for sid in sorted(missing_stock_ids):
            info = stock_info.get(sid, {})
            print(f"  {sid} {info.get('stock_name', '')} ({info.get('type', '')})")

        if not missing_stock_ids:
            print("無需更新，無新公司資料")
            return

        end_date = datetime.today().date().isoformat()
        start_date = (datetime.today() - timedelta(days=365)).date().isoformat()

        for stock_id in sorted(missing_stock_ids):
            try:
                print(f"抓取 {stock_id} 從 {start_date} 到 {end_date} 的資料...")
                df_daily = api.taiwan_stock_daily(
                    stock_id=stock_id,
                    start_date=start_date,
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
                input("請按 Enter 鍵繼續下一檔...")
                conn.rollback()

    finally:
        conn.close()
