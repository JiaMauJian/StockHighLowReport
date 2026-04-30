import requests, os
from dotenv import load_dotenv
load_dotenv()

token = os.environ["FINMIND_TOKEN"]

resp = requests.get(
    "https://api.finmindtrade.com/api/v4/data",
    params={
        "dataset": "TaiwanStockPrice",
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "token": token,
    }
)
body = resp.json()
print(body["msg"], len(body.get("data", [])))