import sqlite3

conn = sqlite3.connect("stock.db")
cur = conn.cursor()
cur.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM stock_daily WHERE stock_id = '9103'")
print("9103:", cur.fetchone())
cur.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM stock_daily WHERE stock_id = '9105'")
print("9105:", cur.fetchone())
conn.close()
