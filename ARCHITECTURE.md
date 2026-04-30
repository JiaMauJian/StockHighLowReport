# 系統架構說明

## 概覽

```
本地端 (stock.db)
  ├─ 平常用 main.py 維護 stock.db（照原本流程不變）
  ├─ 第一次跑 init_high_low_60d.py → 寫進 Turso high_low_60d
  └─ 之後如果需要重算歷史也可以再跑一次

Turso
  └─ 只需要一張 high_low_60d 表（不需要 stock_daily）

GitHub Actions（每天平日 台灣時間 18:00）
  └─ FinMind 抓近 70 天 → pandas 算 60 日滾動 → upsert Turso high_low_60d

Vercel
  └─ 讀 Turso high_low_60d → 顯示圖表
```

---

## Turso

只需要一張表，不需要上傳 `stock_daily`：

```sql
CREATE TABLE high_low_60d (
    date TEXT PRIMARY KEY,
    taiex_close REAL,
    num_lows INTEGER,
    num_highs INTEGER,
    num_traded_stocks INTEGER,
    low_ratio REAL,
    high_ratio REAL
);
```

約 ~2500 筆（10 年每日），非常小。

---

## 本地端維護

`stock.db` 照原本流程維護，不受影響：

```
main.py → update_daily_data_from_last_date.py  （更新每日股價）
       → fetch_missing_stock_data.py           （補缺漏資料）
       → run_high_low_report.py                （產生 Excel 報告）
```

如果本地補了很久以前的歷史資料，重跑一次 `init_high_low_60d.py` 讓 Turso 同步即可。

---

## 初始化（第一次）

從本地 `stock.db` 算完所有歷史資料後寫進 Turso，只需執行一次：

```bash
TURSO_DATABASE_URL=libsql://xxx.turso.io \
TURSO_AUTH_TOKEN=xxx \
python scripts/init_high_low_60d.py
```

---

## GitHub Actions

每天平日自動執行 `scripts/update_turso.py`：

1. 查 `high_low_60d` 最後一筆日期，決定要往前抓幾天
2. 從 FinMind 抓足夠天數的資料（至少 70 天，缺幾天就多抓幾天自動補齊）
3. 用 pandas 算 60 日滾動新高新低（邏輯與 `run_high_low_report.py` option 2 相同）
4. `INSERT OR REPLACE` 回寫 Turso `high_low_60d`

需要設定三個 GitHub Secrets：

| Secret | 說明 |
|--------|------|
| `FINMIND_TOKEN` | FinMind API token |
| `TURSO_DATABASE_URL` | 格式：`libsql://xxx.turso.io` |
| `TURSO_AUTH_TOKEN` | Turso auth token |

---

## Vercel

`api/data.js` 查 Turso `high_low_60d` 回傳 JSON，`public/index.html` 用 Plotly 顯示圖表。

需要設定兩個環境變數：

| 變數 | 說明 |
|------|------|
| `TURSO_DATABASE_URL` | 格式：`libsql://xxx.turso.io` |
| `TURSO_AUTH_TOKEN` | Turso auth token |

---

## 補跑邏輯

若 GitHub Actions 有幾天沒跑，下次執行時會自動補齊：

| 情況 | 缺幾天 | 抓幾天 |
|------|--------|--------|
| 正常每天跑 | 1 | 70 |
| 缺 5 天 | 5 | 75 |
| 缺 30 天 | 30 | 100 |
| 缺 3 個月 | 90 | 160 |
