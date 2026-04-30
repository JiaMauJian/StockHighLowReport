import sqlite3

conn = sqlite3.connect("stock.db")
cur = conn.cursor()
cur.execute("SELECT date, close FROM stock_daily WHERE stock_id = 'TAIEX' ORDER BY date DESC LIMIT 5")
print("TAIEX 最新幾筆：")
for row in cur.fetchall():
    print(" ", row)
conn.close()
