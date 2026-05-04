# 台股儀表板 SOP

---

## 日常（全自動，不需要操作）

GitHub Actions 每天**週一至週五 18:00** 自動執行，更新圖表並部署到 Vercel。

---

## 一、更新市場溫度（建議每週一次，或市場有重大變化時）

```bash
cd D:\StockHighLowReport
python analyze_market.py
```

跑完後會產生：
- `market_temp_latest.json` ← 網頁用
- `market_data_YYYYMMDD.xlsx` ← 你自己留存分析用
- `market_analysis_YYYYMMDD.txt` ← 可貼給 Claude 分析用

push 上去：

```bash
git add market_temp_latest.json
git commit -m "update market temperature"
git push
```

> Vercel 會在幾分鐘內自動重新部署。

---

## 二、新增歷史事件（有重大事件發生時）

編輯 `taiwan_market_events.md`，在對應區段加入新事件，格式參考現有內容。

同步更新 `taiwan_market_events.csv`（格式參考現有欄位）。

push 上去：

```bash
git add taiwan_market_events.md taiwan_market_events.csv
git commit -m "add event: 事件名稱"
git push
```

---

## 三、手動觸發圖表更新（非週一至週五，或臨時想更新）

到 GitHub → Actions → **Update Dashboard** → **Run workflow** → 按 **Run workflow**

---

## 四、把市場資料傳給 Claude 做分析

需要這三個檔案：

| 檔案 | 如何取得 |
|------|---------|
| `market_data_YYYYMMDD.xlsx` | 跑 `analyze_market.py` 產生 |
| `market_analysis_YYYYMMDD.txt` | 同上 |
| `taiwan_market_events.md` | 固定放在專案資料夾 |

開新對話，上傳三個檔案，貼上 `analysis_prompt.md` 裡的 Prompt。

---

## 五、更新程式碼後部署

只要 push 到 GitHub，Vercel 就會自動重新部署，不需要額外操作。

```bash
git add .
git commit -m "說明改了什麼"
git push
```

---

## 網址

| 環境 | 網址 |
|------|------|
| Vercel 線上版 | _(Vercel 部署後填入)_ |
| 本機測試 | `python run_dashboard.py` → 開啟 `dashboard.html` |

---

## 常見問題

**Q：網頁沒有更新？**
→ 到 GitHub Actions 確認最新一次 workflow 是否成功（綠色勾勾）。
→ 失敗的話點進去看錯誤訊息。

**Q：Actions 跑失敗，顯示 API 錯誤？**
→ 確認 GitHub Secrets 的三個 token 是否過期（FINMIND_TOKEN、TURSO_AUTH_TOKEN）。

**Q：想在本機測試，但不想等 API？**
→ 把 `run_dashboard.py` 最後一行改成 `run_dashboard_test()` 跑，用假資料。
