import os
import webbrowser
import json
from datetime import date
from dateutil.relativedelta import relativedelta

import pandas as pd
import requests
from FinMind.data import DataLoader
from dotenv import load_dotenv

load_dotenv()

DAYS = 60
OUTPUT_FILE = "dashboard.html"
MACD_START_DATE = "1990-01-01"


def _read_token():
    token = os.environ.get("FINMIND_TOKEN")
    if not token:
        raise RuntimeError("❌ 找不到 FINMIND_TOKEN，請確認 .env 檔案已設定。")
    return token


def _load_from_turso(start_date: str) -> tuple:
    """從 Turso high_low_60d 讀取已算好的新高新低資料，回傳 (df_hl, df_taiex)。"""
    turso_url   = os.environ.get("TURSO_DATABASE_URL", "").replace("libsql://", "https://")
    turso_token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not turso_url or not turso_token:
        raise RuntimeError("❌ 找不到 TURSO_DATABASE_URL 或 TURSO_AUTH_TOKEN，請確認 .env 檔案已設定。")

    headers = {
        "Authorization": f"Bearer {turso_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": (
                        "SELECT date, taiex_close, num_lows, num_highs, "
                        "num_traded_stocks, low_ratio, high_ratio "
                        "FROM high_low_60d WHERE date >= ? ORDER BY date"
                    ),
                    "args": [{"type": "text", "value": start_date}],
                },
            },
            {"type": "close"},
        ]
    }
    r = requests.post(f"{turso_url}/v2/pipeline", headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    result = r.json()["results"][0]["response"]["result"]

    cols = [c["name"] for c in result["cols"]]
    data = [[cell.get("value") for cell in row] for row in result["rows"]]
    df = pd.DataFrame(data, columns=cols)

    df["date"]               = pd.to_datetime(df["date"])
    df["taiex_close"]        = pd.to_numeric(df["taiex_close"])
    df["num_lows"]           = pd.to_numeric(df["num_lows"]).astype(int)
    df["num_highs"]          = pd.to_numeric(df["num_highs"]).astype(int)
    df["num_traded_stocks"]  = pd.to_numeric(df["num_traded_stocks"]).astype(int)
    df["low_ratio"]          = pd.to_numeric(df["low_ratio"])
    df["high_ratio"]         = pd.to_numeric(df["high_ratio"])

    df_hl    = df[["date", "num_lows", "num_highs", "num_traded_stocks", "low_ratio", "high_ratio"]].copy()
    df_taiex = (df[["date", "taiex_close"]]
                .rename(columns={"taiex_close": "close"})
                .dropna(subset=["close"])
                .reset_index(drop=True))

    return df_hl, df_taiex


def _load_margin(api, start_date: str, end_date: str) -> pd.DataFrame:
    # FinMind 的「大盤融資維持率」計算條件僅包含上市個股資料
    df = api.taiwan_total_exchange_margin_maintenance(
        start_date=start_date, end_date=end_date
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_cnn_fear_greed(token: str, start_date: str, end_date: str) -> pd.DataFrame:
    res = requests.get(
        "https://api.finmindtrade.com/api/v4/data",
        params={"dataset": "CnnFearGreedIndex", "start_date": start_date, "end_date": end_date},
        headers={"Authorization": f"Bearer {token}"},
    )
    df = pd.DataFrame(res.json()["data"])
    df["date"] = pd.to_datetime(df["date"])
    return df


MAX_YEARS = 10


def _calc_macd(series):
    ema12  = series.ewm(span=12, adjust=False).mean()
    ema26  = series.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal, macd - signal


def _fetch_monthly_macd(api, stock_id: str) -> pd.DataFrame:
    daily = api.taiwan_stock_daily(
        stock_id=stock_id,
        start_date=MACD_START_DATE,
        end_date=date.today().strftime("%Y-%m-%d"),
    )
    daily = daily.sort_values("date").reset_index(drop=True)
    daily["ym"] = daily["date"].str[:7]
    df = (
        daily.groupby("ym", sort=True)
        .agg(
            date  =("ym",    "first"),
            open  =("open",  "first"),
            max   =("max",   "max"),
            min   =("min",   "min"),
            close =("close", "last"),
        )
        .reset_index(drop=True)
    )
    df["macd_w"], df["signal_w"], df["hist_w"] = _calc_macd(
        (df["max"] + df["min"] + 2 * df["close"]) / 4
    )
    df["macd_c"], df["signal_c"], df["hist_c"] = _calc_macd(df["close"])
    return df


def _build_summary_html(df_hl, df_taiex, df_margin, df_cnn, df_taiex_macd, df_tpex_macd) -> str:
    """產生頂部 summary bar HTML。"""

    def latest2(df, col):
        """回傳 (最新值, 前一筆值)，可能為 None。"""
        if df is None or df.empty or col not in df.columns:
            return None, None
        s = df.sort_values("date")[col].dropna()
        curr = float(s.iloc[-1]) if len(s) >= 1 else None
        prev = float(s.iloc[-2]) if len(s) >= 2 else None
        return curr, prev

    def delta_tag(curr, prev, fmt, reverse=False):
        """產生帶顏色箭頭的差值 span。"""
        if curr is None or prev is None:
            return ""
        diff = curr - prev
        if diff == 0:
            return '<span class="s-delta neutral">—</span>'
        arrow  = "↑" if diff > 0 else "↓"
        good   = diff > 0 if not reverse else diff < 0
        cls    = "up" if good else "dn"
        return f'<span class="s-delta {cls}">{arrow} {abs(diff):{fmt}}</span>'

    def card(label, value_str, delta_str=""):
        return (
            f'<div class="sc">'
            f'<div class="sc-label">{label}</div>'
            f'<div class="sc-value">{value_str}</div>'
            f'{delta_str}'
            f'</div>'
        )

    # ── 取數值 ─────────────────────────────────────────────────
    latest_date = df_hl["date"].max().strftime("%Y-%m-%d") if not df_hl.empty else "N/A"

    taiex_c, taiex_p   = latest2(df_taiex, "close")
    low_c,   low_p     = latest2(df_hl,    "low_ratio")
    high_c,  high_p    = latest2(df_hl,    "high_ratio")
    margin_c, margin_p = latest2(df_margin, "TotalExchangeMarginMaintenance")
    cnn_c,   cnn_p     = latest2(df_cnn,   "fear_greed")
    taiex_w_osc_c, taiex_w_osc_p = latest2(df_taiex_macd, "hist_w")
    taiex_c_osc_c, taiex_c_osc_p = latest2(df_taiex_macd, "hist_c")
    tpex_w_osc_c,  tpex_w_osc_p  = latest2(df_tpex_macd,  "hist_w")
    tpex_c_osc_c,  tpex_c_osc_p  = latest2(df_tpex_macd,  "hist_c")

    # ── 組合卡片 ───────────────────────────────────────────────
    cards = "".join([
        card("最新日期",      latest_date),
        card("TAIEX 收盤",
             f"{taiex_c:,.0f}" if taiex_c else "N/A",
             delta_tag(taiex_c, taiex_p, ".0f")),
        card("60日新低(%)",
             f"{low_c:.1f}%" if low_c is not None else "N/A",
             delta_tag(low_c, low_p, ".1f", reverse=True)),
        card("60日新高(%)",
             f"{high_c:.1f}%" if high_c is not None else "N/A",
             delta_tag(high_c, high_p, ".1f")),
        card("融資維持率",
             f"{margin_c:.1f}" if margin_c else "N/A",
             delta_tag(margin_c, margin_p, ".1f")),
        card("CNN Fear/Greed",
             f"{cnn_c:.0f}" if cnn_c is not None else "N/A",
             delta_tag(cnn_c, cnn_p, ".0f")),
        card("上市 MACD OSC（三竹）",
             f"{taiex_w_osc_c:.2f}" if taiex_w_osc_c is not None else "N/A",
             delta_tag(taiex_w_osc_c, taiex_w_osc_p, ".2f")),
        card("上市 MACD OSC（鉅亨）",
             f"{taiex_c_osc_c:.2f}" if taiex_c_osc_c is not None else "N/A",
             delta_tag(taiex_c_osc_c, taiex_c_osc_p, ".2f")),
        card("上櫃 MACD OSC（三竹）",
             f"{tpex_w_osc_c:.2f}" if tpex_w_osc_c is not None else "N/A",
             delta_tag(tpex_w_osc_c, tpex_w_osc_p, ".2f")),
        card("上櫃 MACD OSC（鉅亨）",
             f"{tpex_c_osc_c:.2f}" if tpex_c_osc_c is not None else "N/A",
             delta_tag(tpex_c_osc_c, tpex_c_osc_p, ".2f")),
    ])
    return f'<div class="summary-bar">{cards}</div>'


def _date_str(value) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _month_str(value) -> str:
    return f"{str(value)[:7]}-01"


def _month_ms(value) -> int:
    return int(pd.Timestamp(_month_str(value)).timestamp() * 1000)


def _series_points(df: pd.DataFrame, value_col: str) -> list:
    return [
        {"x": _date_str(row["date"]), "y": None if pd.isna(row[value_col]) else float(row[value_col])}
        for _, row in df.sort_values("date").iterrows()
    ]


def _macd_payload(df: pd.DataFrame) -> dict:
    df = df.sort_values("date").reset_index(drop=True)
    candles = [
        {
            "x": _month_ms(row["date"]),
            "t": _month_str(row["date"]),
            "o": float(row["open"]),
            "h": float(row["max"]),
            "l": float(row["min"]),
            "c": float(row["close"]),
        }
        for _, row in df.iterrows()
    ]

    def macd_series(suffix: str) -> dict:
        return {
            "hist": [
                {"x": _month_str(row["date"]), "y": float(row[f"hist_{suffix}"])}
                for _, row in df.iterrows()
            ],
            "dif": [
                {"x": _month_str(row["date"]), "y": float(row[f"macd_{suffix}"])}
                for _, row in df.iterrows()
            ],
            "signal": [
                {"x": _month_str(row["date"]), "y": float(row[f"signal_{suffix}"])}
                for _, row in df.iterrows()
            ],
        }

    return {"candles": candles, "weighted": macd_series("w"), "close": macd_series("c")}


def _build_dashboard_payload(df_hl, df_taiex, df_margin, df_cnn,
                             df_taiex_macd, df_tpex_macd,
                             default_start, end_date) -> dict:
    return {
        "meta": {
            "days": DAYS,
            "defaultStart": default_start,
            "endDate": end_date,
            "generatedAt": datetime_now_taipei(),
        },
        "series": {
            "taiex": _series_points(df_taiex, "close"),
            "lowRatio": _series_points(df_hl, "low_ratio"),
            "highRatio": _series_points(df_hl, "high_ratio"),
            "margin": _series_points(df_margin, "TotalExchangeMarginMaintenance"),
            "cnn": _series_points(df_cnn, "fear_greed"),
        },
        "macd": {
            "taiex": _macd_payload(df_taiex_macd),
            "tpex": _macd_payload(df_tpex_macd),
        },
    }


def datetime_now_taipei() -> str:
    return pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d %H:%M")


def run_dashboard():
    token = _read_token()

    today = date.today()
    end_date = today.strftime("%Y-%m-%d")
    start_date = "1990-01-01"
    default_start = (today - relativedelta(months=6)).strftime("%Y-%m-%d")

    print("從 Turso 讀取新高新低資料...")
    df_hl, df_taiex = _load_from_turso(start_date)
    print(f"Turso 資料讀取完成：{len(df_hl)} 筆，最新日期 {df_hl['date'].max().date()}")

    print("抓取融資維持率資料...")
    api = DataLoader()
    api.login_by_token(api_token=token)
    df_margin = _load_margin(api, start_date, end_date)
    print("融資維持率資料抓取完成")

    print("抓取 CNN Fear/Greed 資料...")
    df_cnn = _load_cnn_fear_greed(token, start_date, end_date)
    print("CNN Fear/Greed 資料抓取完成")

    print("抓取月MACD資料（TAIEX / TPEx）...")
    df_taiex_macd = _fetch_monthly_macd(api, "TAIEX")
    df_tpex_macd  = _fetch_monthly_macd(api, "TPEx")
    print("月MACD資料抓取完成")

    # 以 TAIEX 交易日為主，過濾外部 API 資料
    taiex_dates = set(df_taiex["date"])
    df_margin = df_margin[df_margin["date"].isin(taiex_dates)].reset_index(drop=True)
    df_cnn    = df_cnn[df_cnn["date"].isin(taiex_dates)].reset_index(drop=True)

    payload = _build_dashboard_payload(
        df_hl, df_taiex, df_margin, df_cnn,
        df_taiex_macd, df_tpex_macd,
        default_start, end_date,
    )
    summary = _build_summary_html(df_hl, df_taiex, df_margin, df_cnn, df_taiex_macd, df_tpex_macd)
    _write_html(payload, end_date, summary)


def _load_market_temp_html() -> str:
    """讀取 market_temp_latest.json，回傳市場溫度分頁 HTML。"""
    import json
    json_path = os.path.join(os.path.dirname(__file__), "market_temp_latest.json")
    if not os.path.exists(json_path):
        return """
        <div style="text-align:center; padding:60px; color:#888;">
          <div style="font-size:48px; margin-bottom:16px;">🌡️</div>
          <div style="font-size:18px; font-weight:600; margin-bottom:8px;">尚無市場溫度資料</div>
          <div style="font-size:14px;">請在本機執行 <code>python analyze_market.py</code> 後 push 至 GitHub</div>
        </div>"""
    with open(json_path, encoding="utf-8") as f:
        d = json.load(f)

    avg = d["avg_sentiment"]
    if avg >= 75:   label, color = "過熱（貪婪）", "#dc2626"
    elif avg >= 55: label, color = "偏熱",         "#f97316"
    elif avg >= 45: label, color = "中性",         "#16a34a"
    elif avg >= 25: label, color = "偏冷",         "#2563eb"
    else:           label, color = "過冷（恐懼）", "#7c3aed"

    rows = ""
    for s in d["stats"]:
        bar_w = int(s["sentiment_pct"])
        bar_color = "#dc2626" if s["sentiment_pct"] >= 60 else ("#2563eb" if s["sentiment_pct"] <= 40 else "#f97316")
        rows += f"""
        <tr>
          <td>{s['name']}</td>
          <td style="text-align:right; font-weight:600;">{s['current']:,.2f}</td>
          <td style="text-align:right;">{s['percentile']:.1f}%</td>
          <td style="min-width:160px; padding:0 8px;">
            <div style="background:#eee; border-radius:4px; height:14px; position:relative;">
              <div style="width:{bar_w}%; background:{bar_color}; height:100%; border-radius:4px;"></div>
            </div>
            <div style="font-size:11px; color:#666; text-align:right;">{s['sentiment_pct']:.1f}</div>
          </td>
          <td style="text-align:right; color:#888; font-size:13px;">{s['p10']:.2f}</td>
          <td style="text-align:right; color:#888; font-size:13px;">{s['median']:.2f}</td>
          <td style="text-align:right; color:#888; font-size:13px;">{s['p90']:.2f}</td>
          <td style="text-align:right; color:#aaa; font-size:12px;">{s['n']:,}</td>
        </tr>"""

    return f"""
    <div style="max-width:960px; margin:0 auto;">
      <div style="display:flex; align-items:center; gap:24px; margin-bottom:24px; padding:20px 24px;
                  background:#f8f9fb; border-radius:12px; border:1px solid #e2e8f0;">
        <div>
          <div style="font-size:13px; color:#888; margin-bottom:4px;">整體市場溫度</div>
          <div style="font-size:36px; font-weight:800; color:{color};">{label}</div>
        </div>
        <div style="flex:1;">
          <div style="background:#eee; border-radius:8px; height:20px; position:relative;">
            <div style="width:{int(avg)}%; background:{color}; height:100%; border-radius:8px;"></div>
          </div>
          <div style="display:flex; justify-content:space-between; font-size:12px; color:#888; margin-top:4px;">
            <span>0 極度恐懼</span><span style="font-weight:700; color:{color};">{avg:.1f} / 100</span><span>100 極度貪婪</span>
          </div>
        </div>
        <div style="text-align:right; font-size:13px; color:#aaa;">
          TAIEX {d['taiex']:,.0f}<br>資料日期 {d['latest_date']}<br>更新 {d['generated_at']}
        </div>
      </div>
      <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <thead>
          <tr style="background:#f1f5f9; color:#555;">
            <th style="text-align:left; padding:10px 8px;">指標</th>
            <th style="text-align:right; padding:10px 8px;">當前值</th>
            <th style="text-align:right; padding:10px 8px;">歷史百分位</th>
            <th style="padding:10px 8px;">溫度（0恐懼→100貪婪）</th>
            <th style="text-align:right; padding:10px 8px;">P10</th>
            <th style="text-align:right; padding:10px 8px;">中位數</th>
            <th style="text-align:right; padding:10px 8px;">P90</th>
            <th style="text-align:right; padding:10px 8px;">樣本數</th>
          </tr>
        </thead>
        <tbody style="border-top:2px solid #e2e8f0;">{rows}</tbody>
      </table>
      <div style="font-size:12px; color:#bbb; margin-top:16px; text-align:right;">
        ※ 百分位基準：analyze_market.py 抓取的完整歷史資料（最多 20 年）
      </div>
    </div>"""


def _load_events_html() -> str:
    """讀取 taiwan_market_events.md，回傳歷史事件分頁 HTML（用 marked.js 渲染）。"""
    md_path = os.path.join(os.path.dirname(__file__), "taiwan_market_events.md")
    if not os.path.exists(md_path):
        return """
        <div style="text-align:center; padding:60px; color:#888;">
          <div style="font-size:48px; margin-bottom:16px;">📅</div>
          <div style="font-size:18px; font-weight:600;">找不到 taiwan_market_events.md</div>
        </div>"""
    with open(md_path, encoding="utf-8") as f:
        md_content = f.read()
    # 將 markdown 內容 JS 字串跳脫
    md_escaped = md_content.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    return f"""
    <div id="events-content" style="max-width:960px; margin:0 auto; font-size:14px; line-height:1.7;"></div>
    <script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
    <script>
      document.getElementById('events-content').innerHTML =
        marked.parse(`{md_escaped}`);
    </script>
    <style>
      .md-content h1,h2 {{ border-bottom:1px solid #e2e8f0; padding-bottom:6px; }}
      .md-content table {{ border-collapse:collapse; width:100%; margin:12px 0; }}
      .md-content th {{ background:#f1f5f9; padding:8px 12px; text-align:left; white-space:nowrap; }}
      .md-content td {{ padding:7px 12px; border-bottom:1px solid #f0f0f0; }}
      .md-content tr:hover td {{ background:#fafafa; }}
      .md-content code {{ background:#f1f5f9; padding:1px 5px; border-radius:3px; font-size:16px; }}
      .md-content pre {{ background:#f8f9fb; padding:12px 16px; border-radius:6px; overflow-x:auto; font-size:16px; }}
      .md-content blockquote {{ border-left:4px solid #2979c8; margin:0; padding:8px 16px; background:#f0f6ff; color:#444; }}
    </style>"""



def _write_html(payload: dict, end_date: str, summary_html: str = ""):
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>台股市場監控台</title>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
  <style>
    * {{ box-sizing: border-box; }}
    :root {{
      --bg: #eef2f5;
      --panel: #ffffff;
      --panel-2: #f8fafc;
      --ink: #141821;
      --muted: #667085;
      --line: #d7dde5;
      --blue: #2463eb;
      --red: #d9283a;
      --green: #12805c;
      --amber: #c77700;
      --shadow: 0 18px 45px rgba(24, 33, 45, 0.10);
    }}
    body {{
      margin: 0;
      background:
        linear-gradient(180deg, rgba(19, 24, 33, 0.06), transparent 260px),
        var(--bg);
      color: var(--ink);
      font-family: "Aptos", "Segoe UI", "Noto Sans TC", sans-serif;
    }}
    .container {{ max-width: 1680px; margin: 0 auto; padding: 18px 18px 42px; }}
    .topline {{
      display: flex; align-items: end; justify-content: space-between;
      gap: 16px; margin-bottom: 14px;
    }}
    .brand-kicker {{
      color: var(--blue); font-size: 12px; font-weight: 800;
      letter-spacing: 0.14em; text-transform: uppercase;
    }}
    h1 {{ margin: 2px 0 0; font-size: 28px; line-height: 1.15; }}
    .asof {{ color: var(--muted); font-size: 13px; text-align: right; }}
    .summary-bar {{
      display: grid;
      grid-template-columns: repeat(10, minmax(112px, 1fr));
      gap: 1px;
      background: var(--line);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: var(--shadow);
      margin-bottom: 12px;
    }}
    .sc {{
      min-width: 0; background: var(--panel);
      padding: 12px 14px 11px;
    }}
    .sc-label  {{ font-size: 12px; color: var(--muted); margin-bottom: 5px; white-space: nowrap; }}
    .sc-value  {{ font-size: 23px; font-weight: 850; color: var(--ink); line-height: 1.05; }}
    .s-delta   {{ font-size: 12px; margin-top: 5px; display: block; font-weight: 750; }}
    .s-delta.up {{ color: var(--green); }}
    .s-delta.dn {{ color: var(--red); }}
    .s-delta.neutral {{ color: var(--muted); }}
    .tab-nav {{
      display: flex; flex-wrap: wrap; gap: 6px; margin: 14px 0 12px;
    }}
    .tab-btn {{
      min-height: 36px; padding: 0 15px; font-size: 14px; font-weight: 800;
      border: 1px solid var(--line); border-radius: 8px;
      background: rgba(255,255,255,0.7); color: var(--muted);
      cursor: pointer; transition: transform 0.15s, background 0.15s, color 0.15s;
    }}
    .tab-btn:hover {{ transform: translateY(-1px); color: var(--ink); background: #fff; }}
    .tab-btn.active {{ color: #fff; border-color: #172033; background: #172033; }}
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}
    .toolbar {{
      display: flex; align-items: center; justify-content: space-between;
      gap: 10px; flex-wrap: wrap; margin-bottom: 12px;
      padding: 8px 10px; border: 1px solid var(--line);
      border-radius: 8px; background: rgba(248,250,253,0.86);
    }}
    .tool-section {{ display: flex; gap: 5px; flex-wrap: wrap; align-items: center; }}
    .tool-label {{
      color: var(--muted); font-size: 11px; font-weight: 900;
      letter-spacing: 0.12em; text-transform: uppercase; margin-right: 6px;
    }}
    .tool-btn {{
      min-height: 30px; padding: 0 12px; border-radius: 7px;
      border: 1px solid var(--line); background: #fff; color: var(--ink);
      cursor: pointer; font-size: 13px; font-weight: 800;
    }}
    .tool-btn:hover {{ border-color: #9aa8ba; }}
    .tool-btn.active {{ background: var(--blue); border-color: var(--blue); color: #fff; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap: 12px; align-items: start; }}
    .grid.one-col {{ grid-template-columns:1fr; }}
    .chart-panel {{
      background: var(--panel); border: 1px solid var(--line);
      border-radius: 8px; box-shadow: var(--shadow); overflow: hidden;
    }}
    .chart-head {{
      display: flex; justify-content: space-between; gap: 12px;
      padding: 12px 14px 8px; border-bottom: 1px solid #edf0f4;
    }}
    .chart-title {{ margin: 0; font-size: 15px; font-weight: 900; }}
    .chart-subtitle {{ margin-top: 3px; color: var(--muted); font-size: 12px; }}
    .chart-badge {{
      height: 24px; padding: 4px 8px; border-radius: 999px;
      background: var(--panel-2); color: var(--muted);
      font-size: 11px; font-weight: 850; white-space: nowrap;
    }}
    .chart-wrap {{ height: 360px; padding: 10px 12px 14px; }}
    .chart-node {{ width: 100%; height: 100%; }}
    .macd-stack {{ padding: 10px 12px 14px; }}
    .macd-price {{ height: 370px; }}
    .macd-sub {{ height: 185px; margin-top: 8px; }}
    .empty-state {{ color: var(--muted); padding: 48px 20px; text-align: center; }}
    @media (max-width: 1180px) {{
      .summary-bar {{ grid-template-columns: repeat(4, minmax(120px, 1fr)); }}
      .grid {{ grid-template-columns:1fr !important; }}
    }}
    @media (max-width: 600px) {{
      .container {{ padding: 12px 10px 30px; }}
      .topline {{ display: block; }}
      .asof {{ text-align: left; margin-top: 6px; }}
      .summary-bar {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .sc {{ padding: 10px; }}
      .sc-value {{ font-size: 19px; }}
      .chart-wrap {{ height: 310px; }}
      .macd-price {{ height: 330px; }}
      .macd-sub {{ height: 170px; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="topline">
      <div>
        <div class="brand-kicker">Taiwan Market Console</div>
        <h1>台股市場監控台</h1>
      </div>
      <div class="asof">資料日期 {end_date}<br>產生時間 {payload["meta"]["generatedAt"]}</div>
    </div>
    {summary_html}

    <div class="tab-nav">
      <button class="tab-btn active" onclick="showTab('charts', this)">圖表</button>
      <button class="tab-btn" onclick="showTab('events', this)">歷史事件</button>
      <button class="tab-btn" onclick="showTab('cycles', this)">景氣循環</button>
    </div>

    <div id="tab-charts" class="tab-content active">
      <div class="toolbar">
        <div class="tool-section" id="range-tools">
          <span class="tool-label">Range</span>
          <button class="tool-btn range-btn active" data-range="0.5" onclick="setRange(0.5, this)">半年</button>
          <button class="tool-btn range-btn" data-range="1" onclick="setRange(1, this)">1 年</button>
          <button class="tool-btn range-btn" data-range="3" onclick="setRange(3, this)">3 年</button>
          <button class="tool-btn range-btn" data-range="5" onclick="setRange(5, this)">5 年</button>
          <button class="tool-btn range-btn" data-range="10" onclick="setRange(10, this)">10 年</button>
          <button class="tool-btn range-btn" data-range="all" onclick="setAll(this)">全部</button>
          <button class="tool-btn" onclick="resetZoomAll()">Reset</button>
        </div>
        <div class="tool-section" id="column-tools">
          <span class="tool-label">Layout</span>
          <button class="tool-btn layout-btn active" onclick="setCols(2, this)">2 欄</button>
          <button class="tool-btn layout-btn" onclick="setCols(1, this)">1 欄</button>
        </div>
      </div>
      <div class="grid" id="main-grid">
        <section class="chart-panel">
          <div class="chart-head"><div><h2 class="chart-title">股價創{DAYS}日新低比例</h2><div class="chart-subtitle">新低比例與 TAIEX 收盤價</div></div><div class="chart-badge">Breadth Risk</div></div>
          <div class="chart-wrap"><div class="chart-node" id="chart-low"></div></div>
        </section>
        <section class="chart-panel">
          <div class="chart-head"><div><h2 class="chart-title">股價創{DAYS}日新高比例</h2><div class="chart-subtitle">新高比例與 TAIEX 收盤價</div></div><div class="chart-badge">Market Thrust</div></div>
          <div class="chart-wrap"><div class="chart-node" id="chart-high"></div></div>
        </section>
        <section class="chart-panel">
          <div class="chart-head"><div><h2 class="chart-title">TAIEX vs 融資維持率</h2><div class="chart-subtitle">大盤與槓桿壓力監控</div></div><div class="chart-badge">Margin</div></div>
          <div class="chart-wrap"><div class="chart-node" id="chart-margin"></div></div>
        </section>
        <section class="chart-panel">
          <div class="chart-head"><div><h2 class="chart-title">TAIEX vs CNN Fear/Greed</h2><div class="chart-subtitle">大盤與海外情緒指標</div></div><div class="chart-badge">Sentiment</div></div>
          <div class="chart-wrap"><div class="chart-node" id="chart-cnn"></div></div>
        </section>
        <section class="chart-panel">
          <div class="chart-head"><div><h2 class="chart-title">TAIEX 上市月 MACD</h2><div class="chart-subtitle">月 K、加權價 MACD、收盤價 MACD</div></div><div class="chart-badge">Monthly</div></div>
          <div class="macd-stack">
            <div class="macd-price"><div class="chart-node" id="chart-macd-taiex-price"></div></div>
            <div class="macd-sub"><div class="chart-node" id="chart-macd-taiex-weighted"></div></div>
            <div class="macd-sub"><div class="chart-node" id="chart-macd-taiex-close"></div></div>
          </div>
        </section>
        <section class="chart-panel">
          <div class="chart-head"><div><h2 class="chart-title">TPEx 上櫃月 MACD</h2><div class="chart-subtitle">月 K、加權價 MACD、收盤價 MACD</div></div><div class="chart-badge">Monthly</div></div>
          <div class="macd-stack">
            <div class="macd-price"><div class="chart-node" id="chart-macd-tpex-price"></div></div>
            <div class="macd-sub"><div class="chart-node" id="chart-macd-tpex-weighted"></div></div>
            <div class="macd-sub"><div class="chart-node" id="chart-macd-tpex-close"></div></div>
          </div>
        </section>
      </div>
    </div>

    <div id="tab-events" class="tab-content">
      <div id="events-content" class="md-content empty-state">載入中...</div>
    </div>

    <div id="tab-cycles" class="tab-content">
      <div id="cycles-content" class="md-content empty-state">載入中...</div>
    </div>
  </div>

  <script>
    const DASHBOARD_DATA = {payload_json};
    const charts = {{}};
    const chartIds = [];
    const activeRange = {{ min: DASHBOARD_DATA.meta.defaultStart, max: DASHBOARD_DATA.meta.endDate }};
    const palette = {{
      ink: '#151a24', muted: '#60708a', grid: '#dde5ef', border: '#cfd8e3',
      red: '#e43f52', redSoft: 'rgba(228, 63, 82, 0.42)', green: '#15906f',
      greenSoft: 'rgba(21, 144, 111, 0.38)', blue: '#2563eb', amber: '#d97706'
    }};

    function showTab(name, button) {{
      document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
      button.classList.add('active');
      document.querySelectorAll('.tab-content').forEach(tab => tab.classList.remove('active'));
      document.getElementById(`tab-${{name}}`).classList.add('active');
      setTimeout(resizeCharts, 40);
    }}

    function loadEvents() {{
      const tbody = document.getElementById('events-body');
      if (!tbody || !DASHBOARD_DATA.events?.length) return;
      tbody.innerHTML = DASHBOARD_DATA.events.map(row => `<tr><td>${{row.Date}}</td><td>${{row.Event}}</td><td>${{row.TAIEX}}</td><td>${{row.New_Low_Ratio}}</td><td>${{row.New_High_Ratio}}</td><td>${{row.Margin_Maintenance}}</td><td>${{row.CNN_Fear_Greed}}</td></tr>`).join('');
    }}

    function loadCycles() {{
      const tbody = document.getElementById('cycles-body');
      if (!tbody || !DASHBOARD_DATA.cycles?.length) return;
      tbody.innerHTML = DASHBOARD_DATA.cycles.map(row => `<tr><td>${{row.start_date}}</td><td>${{row.end_date}}</td><td>${{row.days}}</td><td>${{row.trading_days}}</td><td>${{row.start_value}}</td><td>${{row.end_value}}</td><td>${{row.trough_value}}</td><td>${{row.max_drawdown_pct}}</td></tr>`).join('');
    }}

    function initChart(id) {{
      const node = document.getElementById(id);
      if (!node) return;
      const chart = echarts.init(node, null, {{ renderer: 'canvas' }});
      charts[id] = chart;
      chartIds.push(id);
    }}

    function resizeCharts() {{
      chartIds.forEach(id => charts[id]?.resize());
    }}

    function updateRangeButtons(activeButton) {{
      document.querySelectorAll('.range-btn').forEach(btn => btn.classList.remove('active'));
      if (activeButton) activeButton.classList.add('active');
    }}

    function formatDate(d) {{
      return d.toISOString().slice(0, 10);
    }}

    function setRange(years, button) {{
      const end = new Date(`${{DASHBOARD_DATA.meta.endDate}}T00:00:00`);
      const start = new Date(end);
      start.setMonth(start.getMonth() - Math.round(years * 12));
      activeRange.min = formatDate(start);
      activeRange.max = DASHBOARD_DATA.meta.endDate;
      updateRangeButtons(button);
      renderAllCharts();
    }}

    function setAll(button) {{
      activeRange.min = null;
      activeRange.max = null;
      updateRangeButtons(button);
      renderAllCharts();
    }}

    function resetZoomAll() {{
      activeRange.min = DASHBOARD_DATA.meta.defaultStart;
      activeRange.max = DASHBOARD_DATA.meta.endDate;
      const half = document.querySelector('.range-btn[data-range="0.5"]');
      updateRangeButtons(half);
      renderAllCharts();
    }}

    function setCols(cols, button) {{
      document.querySelectorAll('.layout-btn').forEach(btn => btn.classList.remove('active'));
      button.classList.add('active');
      document.querySelectorAll('.grid').forEach(grid => grid.classList.toggle('one-col', cols === 1));
      setTimeout(resizeCharts, 80);
    }}

    function inRangeDate(dateText) {{
      return (!activeRange.min || dateText >= activeRange.min) && (!activeRange.max || dateText <= activeRange.max);
    }}

    function filterPoints(points) {{
      return (points || []).filter(p => inRangeDate(p.x));
    }}

    function monthKey(dateText) {{
      return String(dateText || '').slice(0, 7);
    }}

    function filterMonthPoints(points) {{
      const minMonth = activeRange.min ? monthKey(activeRange.min) : null;
      const maxMonth = activeRange.max ? monthKey(activeRange.max) : null;
      return (points || []).filter(p => {{
        const key = monthKey(p.x);
        return (!minMonth || key >= minMonth) && (!maxMonth || key <= maxMonth);
      }});
    }}

    function filterCandles(candles) {{
      const minMonth = activeRange.min ? monthKey(activeRange.min) : null;
      const maxMonth = activeRange.max ? monthKey(activeRange.max) : null;
      return (candles || []).filter(c => {{
        const key = monthKey(c.t || c.x);
        return (!minMonth || key >= minMonth) && (!maxMonth || key <= maxMonth);
      }});
    }}

    function alignToLabels(points, labels) {{
      const map = new Map((points || []).map(p => [p.x, p.y]));
      return labels.map(label => map.has(label) ? map.get(label) : null);
    }}

    function finiteValues(values) {{
      return (values || []).filter(v => Number.isFinite(v));
    }}

    function yRange(values, padRatio = 0.08, floorZero = false) {{
      const nums = finiteValues(values);
      if (!nums.length) return {{ min: 0, max: 1 }};
      let min = Math.min(...nums);
      let max = Math.max(...nums);
      const span = Math.max(max - min, Math.abs(max) * 0.02, 1);
      min -= span * padRatio;
      max += span * padRatio;
      if (floorZero) min = Math.min(0, min);
      return {{ min, max }};
    }}

    function monthTickInfo(labels) {{
      const starts = [];
      let last = '';
      labels.forEach((label, index) => {{
        const month = String(label).slice(0, 7);
        if (month !== last) {{
          starts.push(index);
          last = month;
        }}
      }});
      const months = starts.length;
      const yearTicks = starts.filter(index => String(labels[index]).slice(5, 7) === '01');
      const required = new Set([starts[0], ...yearTicks]);
      let selected = starts;
      if (months > 120) {{
        selected = starts.filter(index => required.has(index));
      }} else {{
        const stride = months > 72 ? 6 : months > 36 ? 3 : 1;
        selected = starts.filter((index, i) => required.has(index) || i % stride === 0);
      }}
      selected = selected.sort((a, b) => a - b);
      return {{
        ticks: new Set(selected),
        first: selected.length ? selected[0] : 0
      }};
    }}

    function monthAxisLabel(value, index, firstTick) {{
      const text = String(value || '');
      const year = text.slice(0, 4);
      const month = text.slice(5, 7);
      if (!year || !month) return text;
      if (index === firstTick || month === '01') return `{{year|${{year}}}}`;
      return month;
    }}

    function xAxis(labels, boundaryGap = false) {{
      const tickInfo = monthTickInfo(labels);
      return {{
        type: 'category', data: labels, boundaryGap,
        axisTick: {{ show: false }},
        axisLine: {{ lineStyle: {{ color: '#b8c4d4' }} }},
        axisLabel: {{
          color: palette.muted,
          margin: 9,
          hideOverlap: true,
          rich: {{
            year: {{ fontWeight: 800, color: '#253656' }}
          }},
          interval: index => tickInfo.ticks.has(index),
          formatter: (value, index) => monthAxisLabel(value, index, tickInfo.first)
        }}
      }};
    }}

    function formatTooltipValue(value, digits = 2) {{
      const num = Number(value);
      if (!Number.isFinite(num)) return '-';
      return num.toLocaleString(undefined, {{ minimumFractionDigits: digits, maximumFractionDigits: digits }});
    }}

    function candleTooltip(params) {{
      const item = Array.isArray(params) ? params[0] : params;
      const raw = Array.isArray(item?.data) ? item.data : (item?.value || []);
      const values = raw.length >= 5 ? raw.slice(1, 5) : raw;
      return `
        <div style="font-weight:400; margin-bottom:6px;">${{item?.axisValue || ''}}</div>
        <div>${{item?.marker || ''}}月 K</div>
        <div>開 <span style="float:right; margin-left:18px;">${{formatTooltipValue(values[0], 1)}}</span></div>
        <div>高 <span style="float:right; margin-left:18px;">${{formatTooltipValue(values[3], 1)}}</span></div>
        <div>收 <span style="float:right; margin-left:18px;">${{formatTooltipValue(values[1], 1)}}</span></div>
        <div>低 <span style="float:right; margin-left:18px;">${{formatTooltipValue(values[2], 1)}}</span></div>
      `;
    }}

    function tooltipMarker(color) {{
      return `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${{color}};margin-right:6px;"></span>`;
    }}

    function macdTooltip(params) {{
      const rows = (Array.isArray(params) ? params : [params]).map(item => {{
        const value = Array.isArray(item.value) ? item.value[item.value.length - 1] : item.value;
        const color = item.seriesName === 'OSC'
          ? (Number(value) >= 0 ? 'rgba(228,63,82,0.58)' : 'rgba(21,144,111,0.54)')
          : (item.seriesName === 'DIF' ? '#245fff' : palette.amber);
        return `
        <div>${{tooltipMarker(color)}}${{item.seriesName}}
          <span style="float:right; margin-left:18px;">${{formatTooltipValue(value, 3)}}</span>
        </div>
      `;
      }}).join('');
      return `<div style="font-weight:400; margin-bottom:6px;">${{params?.[0]?.axisValue || ''}}</div>${{rows}}`;
    }}

    function taiexFirstTooltip(params) {{
      const items = Array.isArray(params) ? [...params] : [params];
      items.sort((a, b) => (a.seriesName === 'TAIEX' ? -1 : 0) - (b.seriesName === 'TAIEX' ? -1 : 0));
      const rows = items.map(item => `
        <div>${{item.marker || ''}}${{item.seriesName}}
          <span style="float:right; margin-left:18px;">${{formatTooltipValue(item.value, 2)}}</span>
        </div>
      `).join('');
      return `<div style="font-weight:400; margin-bottom:6px;">${{items?.[0]?.axisValue || ''}}</div>${{rows}}`;
    }}

    function tooltip(formatter = null) {{
      const options = {{
        trigger: 'axis',
        confine: true,
        axisPointer: {{ type: 'cross', label: {{ show: false }} }},
        backgroundColor: 'rgba(248, 250, 252, 0.98)',
        borderColor: '#cbd5e1',
        borderWidth: 1,
        padding: [10, 12],
        extraCssText: 'box-shadow:0 10px 24px rgba(15,23,42,.14); border-radius:6px;',
        textStyle: {{ color: '#172033', fontSize: 12, fontWeight: 400 }}
      }};
      if (formatter) options.formatter = formatter;
      return options;
    }}

    function xDataZoom() {{
      return [{{
        type: 'inside',
        xAxisIndex: 0,
        filterMode: 'none',
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
        moveOnMouseWheel: false,
        preventDefaultMouseMove: true
      }}];
    }}

    function grid(top = 34, bottom = 32, right = 72) {{
      return {{ left: 64, right, top, bottom, containLabel: false }};
    }}

    function valueAxis(name, range, position = 'left', split = true) {{
      return {{
        type: 'value', name, position,
        min: range?.min, max: range?.max,
        nameLocation: 'middle', nameGap: position === 'right' ? 48 : 42,
        nameTextStyle: {{ color: '#4d5d75', fontWeight: 800 }},
        axisLabel: {{ color: palette.muted, formatter: value => Number(value).toLocaleString(undefined, {{ maximumFractionDigits: 1 }}) }},
        axisLine: {{ lineStyle: {{ color: '#b8c4d4' }} }},
        axisTick: {{ show: false }},
        splitLine: {{ show: split, lineStyle: {{ color: palette.grid }} }}
      }};
    }}

    function renderMainChart(id, barLabel, barPoints, barColor, lineLabel, linePoints, leftTitle, rightTitle) {{
      const visibleLine = filterPoints(linePoints);
      const labels = visibleLine.map(p => p.x);
      const bars = alignToLabels(filterPoints(barPoints), labels);
      const line = visibleLine.map(p => p.y);
      const right = yRange(line, 0.08, false);
      charts[id]?.setOption({{
        animation: false, color: [barColor, palette.ink], textStyle: {{ fontFamily: 'Aptos, Segoe UI, Noto Sans TC, sans-serif' }},
        grid: grid(38, 34, 76), tooltip: tooltip(taiexFirstTooltip),
        dataZoom: xDataZoom(),
        legend: {{ top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: {{ color: palette.muted, fontWeight: 600 }} }},
        xAxis: xAxis(labels),
        yAxis: [valueAxis(rightTitle, right), valueAxis(leftTitle, {{ min: 0, max: 100 }}, 'right', false)],
        series: [
          {{ name: barLabel, type: 'bar', yAxisIndex: 1, data: bars, barWidth: '72%', itemStyle: {{ color: barColor }} }},
          {{ name: lineLabel, type: 'line', yAxisIndex: 0, data: line, symbol: 'none', lineStyle: {{ color: palette.ink, width: 2 }}, itemStyle: {{ color: palette.ink }}, connectNulls: true }}
        ]
      }}, true);
    }}

    function renderLineDualChart(id, leftLabel, leftPoints, rightLabel, rightPoints, rightColor, rightTitle, fixedRight) {{
      const visibleLeft = filterPoints(leftPoints);
      const labels = visibleLeft.map(p => p.x);
      const left = visibleLeft.map(p => p.y);
      const rightValues = alignToLabels(filterPoints(rightPoints), labels);
      charts[id]?.setOption({{
        animation: false, color: [palette.ink, rightColor], textStyle: {{ fontFamily: 'Aptos, Segoe UI, Noto Sans TC, sans-serif' }},
        grid: grid(38, 34, 76), tooltip: tooltip(),
        dataZoom: xDataZoom(),
        legend: {{ top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: {{ color: palette.muted, fontWeight: 600 }} }},
        xAxis: xAxis(labels),
        yAxis: [valueAxis('TAIEX', yRange(left, 0.08, false)), valueAxis(rightTitle, fixedRight || yRange(rightValues, 0.10, false), 'right', false)],
        series: [
          {{ name: leftLabel, type: 'line', yAxisIndex: 0, data: left, symbol: 'none', lineStyle: {{ color: palette.ink, width: 2 }}, itemStyle: {{ color: palette.ink }} }},
          {{ name: rightLabel, type: 'line', yAxisIndex: 1, data: rightValues, symbol: 'none', lineStyle: {{ color: rightColor, width: 2 }}, itemStyle: {{ color: rightColor }} }}
        ]
      }}, true);
    }}

    function renderCandleChart(id, candles, axisName) {{
      const visible = filterCandles(candles);
      const labels = visible.map(c => String(c.t || c.x).slice(0, 7));
      const rows = visible.map(c => [c.o, c.c, c.l, c.h]);
      const range = yRange(visible.flatMap(c => [c.l, c.h]), 0.08, false);
      charts[id]?.setOption({{
        animation: false, tooltip: tooltip(candleTooltip), dataZoom: xDataZoom(), grid: {{ left: 74, right: 44, top: 20, bottom: 28, containLabel: false }},
        xAxis: xAxis(labels, true), yAxis: valueAxis(axisName, range, 'left', true),
        series: [{{
          name: '月 K', type: 'candlestick', data: rows,
          itemStyle: {{ color: 'rgba(228,63,82,0.45)', color0: 'rgba(21,144,111,0.45)', borderColor: '#ff6074', borderColor0: '#46b8a8' }}
        }}]
      }}, true);
    }}

    function renderMacdLineChart(id, data, axisName, displayTitle) {{
      const hist = filterMonthPoints(data?.hist || []);
      const labels = hist.map(p => p.x.slice(0, 7));
      const values = {{
        hist: hist.map(p => p.y),
        dif: alignToLabels(filterMonthPoints(data?.dif || []), hist.map(p => p.x)),
        signal: alignToLabels(filterMonthPoints(data?.signal || []), hist.map(p => p.x))
      }};
      const range = yRange([...values.hist, ...values.dif, ...values.signal], 0.14, false);
      charts[id]?.setOption({{
        animation: false, tooltip: tooltip(macdTooltip),
        title: {{ text: displayTitle || axisName, left: 'center', top: 0, textStyle: {{ color: '#0f2748', fontSize: 14, fontWeight: 700 }} }},
        dataZoom: xDataZoom(),
        grid: {{ left: 74, right: 44, top: 34, bottom: 28, containLabel: false }},
        xAxis: xAxis(labels, true), yAxis: valueAxis(axisName, range, 'left', true),
        series: [
          {{ name: 'OSC', type: 'bar', data: values.hist, barWidth: '62%', itemStyle: {{ color: params => params.value >= 0 ? 'rgba(228,63,82,0.38)' : 'rgba(21,144,111,0.36)' }} }},
          {{ name: 'DIF', type: 'line', data: values.dif, symbol: 'none', lineStyle: {{ color: '#245fff', width: 2 }}, itemStyle: {{ color: '#245fff' }} }},
          {{ name: 'MACD9', type: 'line', data: values.signal, symbol: 'none', lineStyle: {{ color: palette.amber, width: 2 }}, itemStyle: {{ color: palette.amber }} }}
        ]
      }}, true);
    }}

    function renderMacdGroup(prefix, data) {{
      const marketName = prefix === 'taiex' ? 'TAIEX 上市' : 'TPEx 上櫃';
      const axisName = prefix === 'taiex' ? 'TAIEX' : 'TPEx';
      renderCandleChart(`chart-macd-${{prefix}}-price`, data.candles || [], axisName);
      renderMacdLineChart(`chart-macd-${{prefix}}-weighted`, data.weighted || {{}}, 'MACD', `${{marketName}} MACD (H+L+2C)/4（三竹/玩股）`);
      renderMacdLineChart(`chart-macd-${{prefix}}-close`, data.close || {{}}, 'MACD', `${{marketName}} MACD close（鉅亨）`);
    }}

    function renderAllCharts() {{
      const s = DASHBOARD_DATA.series;
      renderMainChart('chart-low', `${{DASHBOARD_DATA.meta.days}}日新低(%)`, s.lowRatio, palette.redSoft, 'TAIEX', s.taiex, '新低比例 (%)', 'TAIEX');
      renderMainChart('chart-high', `${{DASHBOARD_DATA.meta.days}}日新高(%)`, s.highRatio, palette.greenSoft, 'TAIEX', s.taiex, '新高比例 (%)', 'TAIEX');
      renderLineDualChart('chart-margin', 'TAIEX', s.taiex, '融資維持率', s.margin, palette.red, '融資維持率');
      renderLineDualChart('chart-cnn', 'TAIEX', s.taiex, 'CNN Fear/Greed', s.cnn, palette.amber, 'Fear/Greed', {{ min: 0, max: 100 }});
      renderMacdGroup('taiex', DASHBOARD_DATA.macd.taiex);
      renderMacdGroup('tpex', DASHBOARD_DATA.macd.tpex);
      resizeCharts();
    }}

    function initCharts() {{
      ['chart-low', 'chart-high', 'chart-margin', 'chart-cnn', 'chart-macd-taiex-price', 'chart-macd-taiex-weighted', 'chart-macd-taiex-close', 'chart-macd-tpex-price', 'chart-macd-tpex-weighted', 'chart-macd-tpex-close'].forEach(initChart);
      renderAllCharts();
      loadEvents();
      loadCycles();
    }}

    window.addEventListener('resize', resizeCharts);
    window.addEventListener('DOMContentLoaded', initCharts);
  </script>
  <script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
  <style>
    .md-content {{
      max-width: 1200px; margin: 0 auto; padding: 16px 0;
      font-size: 18px; line-height: 1.8; color: #1a1a1a;
      text-align: left;
    }}
    .md-content h1 {{
      font-size: 22px; font-weight: 700; color: #111;
      border-bottom: 2px solid var(--blue); padding-bottom: 8px; margin-top: 32px; margin-bottom: 16px;
      text-align: left;
    }}
    .md-content h2 {{
      font-size: 20px; font-weight: 700; color: #1a1a1a;
      border-left: 4px solid var(--blue); padding-left: 12px;
      margin-top: 28px; margin-bottom: 10px; border-bottom: none;
      text-align: left;
    }}
    .md-content h3 {{
      font-size: 18px; font-weight: 600; color: #333;
      margin-top: 20px; margin-bottom: 6px;
      text-align: left;
    }}
    .md-content ul, .md-content ol {{
      color: #1a1a1a; padding-left: 24px; margin: 6px 0;
      text-align: left;
    }}
    .md-content li {{ margin-bottom: 4px; color: #1a1a1a; }}
    .md-content p {{ margin: 8px 0; text-align: left; color: #1a1a1a; }}
    .md-content strong {{ color: #111; }}
    .md-content table {{
      border-collapse: collapse; width: 100%; margin: 20px 0;
    }}
    .md-content th {{
      background: #f1f5f9; padding: 9px 14px;
      text-align: left; color: #333; white-space: nowrap;
      border-bottom: 2px solid #d1d9e6;
    }}
    .md-content td {{
      padding: 8px 14px; border-bottom: 1px solid #eef0f4; color: #1a1a1a;
      text-align: left;
    }}
    .md-content tr:hover td {{ background: #fafbff; }}
    .md-content code {{
      background: #f1f5f9; padding: 2px 6px; border-radius: 4px;
      font-size: 13px; color: #c7254e;
    }}
    .md-content pre {{
      background: #f8f9fb; padding: 14px 18px; border-radius: 8px; overflow-x: auto;
    }}
    .md-content pre code {{
      background: none; padding: 0; color: #2d2d2d; font-size: 16px;
    }}
    .md-content blockquote {{
      border-left: 4px solid var(--blue); margin: 12px 0;
      padding: 10px 18px; background: #f0f6ff; color: #444;
      border-radius: 0 6px 6px 0;
    }}
    .md-content hr {{
      border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;
    }}
  </style>
</body>
</html>"""
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard 已產生：{os.path.abspath(OUTPUT_FILE)}")
    webbrowser.open(f"file:///{os.path.abspath(OUTPUT_FILE)}")


def _mock_macd_data(n_months: int = 120) -> pd.DataFrame:
    import numpy as np
    rng = np.random.default_rng(42)
    months = pd.date_range("2016-01", periods=n_months, freq="MS")
    monthly_ret = rng.normal(0.0, 0.05, n_months)
    price = 300 * np.cumprod(1 + monthly_ret)
    hi  = price * (1 + rng.uniform(0.01, 0.04, n_months))
    lo  = price * (1 - rng.uniform(0.01, 0.04, n_months))
    op  = price * (1 + rng.normal(0, 0.01, n_months))
    df = pd.DataFrame({
        "date":  months.strftime("%Y-%m"),
        "open":  op.round(2),
        "max":   hi.round(2),
        "min":   lo.round(2),
        "close": price.round(2),
    })
    df["macd_w"], df["signal_w"], df["hist_w"] = _calc_macd(
        (df["max"] + df["min"] + 2 * df["close"]) / 4
    )
    df["macd_c"], df["signal_c"], df["hist_c"] = _calc_macd(df["close"])
    return df


def _mock_main_data(n_days: int = 365 * 10) -> tuple:
    import numpy as np
    rng = np.random.default_rng(0)
    dates = pd.bdate_range(end=date.today(), periods=n_days)

    # TAIEX：從 15000 走 GBM
    ret = rng.normal(0.0003, 0.01, n_days)
    taiex = 15000 * np.cumprod(1 + ret)
    df_taiex = pd.DataFrame({"date": dates, "close": taiex.round(2)})

    # 新高新低比例：隨機 0~100%，與 TAIEX 方向略相關
    trend = pd.Series(ret).rolling(20).mean().fillna(0).values
    low_ratio  = np.clip(20 - trend * 500 + rng.normal(0, 8, n_days), 0, 100).round(2)
    high_ratio = np.clip(20 + trend * 500 + rng.normal(0, 8, n_days), 0, 100).round(2)
    df_hl = pd.DataFrame({
        "date":               dates,
        "low_ratio":          low_ratio,
        "high_ratio":         high_ratio,
        "num_lows":           (low_ratio * 19).astype(int),
        "num_highs":          (high_ratio * 19).astype(int),
        "num_traded_stocks":  [1900] * n_days,
    })

    # 融資維持率：130~180 之間波動
    margin = 155 + rng.normal(0, 8, n_days)
    df_margin = pd.DataFrame({
        "date": dates,
        "TotalExchangeMarginMaintenance": margin.round(1),
    })

    # CNN Fear/Greed：0~100
    fg = np.clip(50 + trend * 2000 + rng.normal(0, 12, n_days), 0, 100)
    df_cnn = pd.DataFrame({"date": dates, "fear_greed": fg.round(1)})

    return df_hl, df_taiex, df_margin, df_cnn


def run_dashboard_test():
    today         = date.today()
    end_date      = today.strftime("%Y-%m-%d")
    default_start = (today - relativedelta(months=6)).strftime("%Y-%m-%d")

    df_hl, df_taiex, df_margin, df_cnn = _mock_main_data()
    df_taiex_macd = _mock_macd_data()
    df_tpex_macd  = _mock_macd_data()

    payload = _build_dashboard_payload(
        df_hl, df_taiex, df_margin, df_cnn,
        df_taiex_macd, df_tpex_macd,
        default_start, end_date,
    )
    summary = _build_summary_html(df_hl, df_taiex, df_margin, df_cnn, df_taiex_macd, df_tpex_macd)
    _write_html(payload, end_date, summary)


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        run_dashboard_test()
    else:
        run_dashboard()
