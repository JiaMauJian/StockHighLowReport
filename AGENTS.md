# StockHighLowReport — Codex 指引

## FinMind API

使用 FinMind 相關 API 或 DataLoader 方法有疑問時，先閱讀專案內的 `llms-full.txt`，裡面有完整的 API 說明、dataset 名稱、欄位結構與參數格式。

## TaiwanStockPrice 過濾規則

使用 `TaiwanStockPrice` 取得資料後，需過濾出台股一般股票，參考 `fetch_missing_stock_data.py` 的寫法：

```python
df = df[df["stock_id"].apply(lambda x: bool(re.fullmatch(r"[1-9]\d{3}", x)))]
df = df[df["date"] == df["date"].max()]
df = df.drop_duplicates(subset=["stock_id"])
df = df[df["type"].isin(["twse", "tpex"])]
df = df[df["industry_category"] != "存託憑證"]  # 排除 DR 股
df = df.sort_values(by="stock_id")
```
