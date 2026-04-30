import os
import requests
import pandas as pd
from dotenv import load_dotenv
from FinMind.data import DataLoader

load_dotenv()
token = os.environ["FINMIND_TOKEN"]

api = DataLoader()
api.login_by_token(api_token=token)

FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = token

# ── 測試：TaiwanStockPrice 不帶 data_id，加 end_date 只會回傳 1 天 ──
print("=== 測試 TaiwanStockPrice start_date + end_date（預期只有 1 天）===")
resp = requests.get(
    FINMIND_API_URL,
    headers={"Authorization": f"Bearer {FINMIND_TOKEN}"},
    params={
        "dataset": "TaiwanStockPrice",
        "start_date": "2026-02-11",
        "end_date": "2026-04-30",
    },
    timeout=120,
)
resp.raise_for_status()
raw = resp.json().get("data", [])
df = pd.DataFrame(raw)[["date", "stock_id", "close"]] if raw else pd.DataFrame()
print(f"Fetched {len(df)} rows, {df['date'].nunique() if not df.empty else 0} trading days")
if not df.empty:
    print(df["date"].unique())

# ── 測試：逐日 loop 抓 3 天 ──
print("\n=== 測試逐日 loop（抓 3 個交易日）===")
test_dates = ["2026-04-28", "2026-04-29", "2026-04-30"]
frames = []
for d in test_dates:
    r = requests.get(
        FINMIND_API_URL,
        headers={"Authorization": f"Bearer {FINMIND_TOKEN}"},
        params={"dataset": "TaiwanStockPrice", "start_date": d},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json().get("data", [])
    if data:
        frames.append(pd.DataFrame(data)[["date", "stock_id", "close"]])
        print(f"  {d}: {len(data)} stocks")
    else:
        print(f"  {d}: no data")

if frames:
    df_all = pd.concat(frames, ignore_index=True)
    print(f"Total: {len(df_all)} rows, {df_all['date'].nunique()} trading days")