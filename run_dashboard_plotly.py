import os
import webbrowser
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

import pandas as pd
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from FinMind.data import DataLoader
from dotenv import load_dotenv

load_dotenv()

DAYS = 60
OUTPUT_FILE = "dashboard_plotly.html"
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


def _build_macd_pair_fig(df: pd.DataFrame, title: str, suffix: str, subtitle: str, default_start: str = None) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.56, 0.44],
        subplot_titles=[
            f"{title} K\u7dda",
            f"{title} MACD {subtitle}",
        ],
        vertical_spacing=0.06,
    )
    fig.add_trace(
        go.Candlestick(
            x=df["date"],
            open=df["open"], high=df["max"], low=df["min"], close=df["close"],
            name=title,
            increasing_line_color="red",
            decreasing_line_color="green",
        ),
        row=1, col=1,
    )

    colors = ["red" if v >= 0 else "green" for v in df[f"hist_{suffix}"]]
    fig.add_trace(
        go.Bar(
            x=df["date"], y=df[f"hist_{suffix}"],
            name=f"OSC {subtitle}", marker_color=colors, opacity=0.5,
            hovertemplate="OSC : %{y:.2f}<extra></extra>",
        ),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["date"], y=df[f"macd_{suffix}"],
            name=f"DIF {subtitle}", line=dict(color="blue", width=1),
            hovertemplate="DIF : %{y:.2f}<extra></extra>",
        ),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df["date"], y=df[f"signal_{suffix}"],
            name=f"MACD9 {subtitle}", line=dict(color="orange", width=1),
            hovertemplate="MACD9 : %{y:.2f}<extra></extra>",
        ),
        row=2, col=1,
    )

    start_ym = (
        default_start[:7]
        if default_start
        else (date.today() - relativedelta(years=3, months=1)).strftime("%Y-%m")
    )
    end_ym = (date.today() + relativedelta(months=2)).strftime("%Y-%m")
    fig.update_layout(
        height=720,
        autosize=True,
        template="plotly_white",
        showlegend=False,
        margin=dict(t=52, b=42, l=62, r=28),
        hoverlabel=dict(font_size=13),
        hovermode="x unified",
        xaxis_rangeslider_visible=False,
        xaxis2_rangeslider_visible=False,
    )
    fig.update_xaxes(
        range=[start_ym, end_ym],
        showticklabels=True,
        tickformat="%Y-%m",
        showspikes=True,
        spikesnap="cursor",
        spikemode="across",
        spikethickness=1,
        spikecolor="#888",
        spikedash="dot",
    )
    fig.update_xaxes(showticklabels=False, row=1, col=1)
    fig.update_yaxes(title_text=title, row=1, col=1)
    fig.update_yaxes(title_text=f"MACD {subtitle}", row=2, col=1)
    return fig

ALL_PLOT_IDS = [
    "plot-low", "plot-high", "plot-margin", "plot-cnn",
    "plot-macd-taiex-w", "plot-macd-taiex-c",
    "plot-macd-tpex-w", "plot-macd-tpex-c",
]


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


def _build_main_chart(title: str, holidays: list, default_start: str, end_date: str, height: int = 400) -> go.Figure:
    """建立單張主圖（secondary_y）共用版型。"""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.update_layout(
        title_text=title,
        height=height,
        template="plotly_white",
        hoverlabel=dict(font_size=13),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=50, b=30, l=55, r=55),
    )
    fig.update_xaxes(
        range=[default_start, end_date],
        rangeslider=dict(visible=False),
        showticklabels=True,
        showline=True, linecolor="#444", mirror=True,
        rangebreaks=[dict(bounds=["sat", "mon"]), dict(values=holidays)],
        tickformat="%Y-%m-%d",
        showspikes=True, spikesnap="cursor", spikemode="across",
        spikethickness=1, spikecolor="#888", spikedash="dot",
    )
    fig.update_yaxes(showline=True, linecolor="#444", mirror=True)
    fig.update_layout(bargap=0)
    return fig


def _build_six_charts(df_hl, df_taiex, df_margin, df_cnn,
                      df_taiex_macd, df_tpex_macd,
                      holidays, default_start, end_date):
    """建立六張圖，回傳 list[go.Figure]，順序對應 ALL_PLOT_IDS。"""

    # ── 圖1：60日新低 ─────────────────────────────────────────
    fig_low = _build_main_chart(f"股價創{DAYS}日新低比例", holidays, default_start, end_date)
    fig_low.add_trace(
        go.Bar(x=df_hl["date"], y=df_hl["low_ratio"],
               name=f"{DAYS}日新低(%)", marker_color="red", opacity=0.8),
    )
    fig_low.add_trace(
        go.Scatter(x=df_taiex["date"], y=df_taiex["close"],
                   name="TAIEX", line=dict(color="black"), mode="lines", showlegend=False),
        secondary_y=True,
    )
    fig_low.update_yaxes(title_text="比例 (%)", range=[0, 100], secondary_y=False)
    fig_low.update_yaxes(title_text="TAIEX", secondary_y=True)
    fig_low.update_traces(xhoverformat="%Y-%m-%d")

    # ── 圖2：60日新高 ─────────────────────────────────────────
    fig_high = _build_main_chart(f"股價創{DAYS}日新高比例", holidays, default_start, end_date)
    fig_high.add_trace(
        go.Bar(x=df_hl["date"], y=df_hl["high_ratio"],
               name=f"{DAYS}日新高(%)", marker_color="green", opacity=0.8),
    )
    fig_high.add_trace(
        go.Scatter(x=df_taiex["date"], y=df_taiex["close"],
                   name="TAIEX", line=dict(color="black"), mode="lines", showlegend=False),
        secondary_y=True,
    )
    fig_high.update_yaxes(title_text="比例 (%)", range=[0, 100], secondary_y=False)
    fig_high.update_yaxes(title_text="TAIEX", secondary_y=True)
    fig_high.update_traces(xhoverformat="%Y-%m-%d")

    # ── 圖3：融資維持率 ───────────────────────────────────────
    fig_margin = _build_main_chart("TAIEX vs 融資維持率", holidays, default_start, end_date)
    fig_margin.add_trace(
        go.Scatter(x=df_taiex["date"], y=df_taiex["close"],
                   name="TAIEX", line=dict(color="black"), mode="lines"),
    )
    fig_margin.add_trace(
        go.Scatter(x=df_margin["date"], y=df_margin["TotalExchangeMarginMaintenance"],
                   name="融資維持率", line=dict(color="red"), mode="lines"),
        secondary_y=True,
    )
    fig_margin.update_yaxes(title_text="TAIEX", secondary_y=False)
    fig_margin.update_yaxes(title_text="融資維持率", secondary_y=True)
    fig_margin.update_traces(xhoverformat="%Y-%m-%d")

    # ── 圖4：CNN Fear/Greed ───────────────────────────────────
    fig_cnn = _build_main_chart("TAIEX vs CNN Fear/Greed", holidays, default_start, end_date)
    fig_cnn.add_trace(
        go.Scatter(x=df_taiex["date"], y=df_taiex["close"],
                   name="TAIEX", line=dict(color="black"), mode="lines"),
    )
    fig_cnn.add_trace(
        go.Scatter(x=df_cnn["date"], y=df_cnn["fear_greed"],
                   name="CNN Fear/Greed", line=dict(color="orange"), mode="lines"),
        secondary_y=True,
    )
    fig_cnn.update_yaxes(title_text="TAIEX", secondary_y=False)
    fig_cnn.update_yaxes(title_text="Fear/Greed", range=[0, 100], secondary_y=True)
    fig_cnn.update_traces(xhoverformat="%Y-%m-%d")

    # MACD ???????? K ? + ?? MACD ??
    fig_taiex_macd_w = _build_macd_pair_fig(df_taiex_macd, "TAIEX \u4e0a\u5e02", "w", "(H+L+2C)/4\uff08\u4e09\u7af9/\u73a9\u80a1\uff09", default_start)
    fig_taiex_macd_c = _build_macd_pair_fig(df_taiex_macd, "TAIEX \u4e0a\u5e02", "c", "Close\uff08\u9245\u4ea8\uff09", default_start)
    fig_tpex_macd_w  = _build_macd_pair_fig(df_tpex_macd,  "TPEx \u4e0a\u6ac3",  "w", "(H+L+2C)/4\uff08\u4e09\u7af9/\u73a9\u80a1\uff09", default_start)
    fig_tpex_macd_c  = _build_macd_pair_fig(df_tpex_macd,  "TPEx \u4e0a\u6ac3",  "c", "Close\uff08\u9245\u4ea8\uff09", default_start)

    return [
        fig_low, fig_high, fig_margin, fig_cnn,
        fig_taiex_macd_w, fig_taiex_macd_c,
        fig_tpex_macd_w, fig_tpex_macd_c,
    ]

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

    # 從 TAIEX 交易日推算出需要跳過的非交易日（假日）
    all_days = pd.date_range(start=df_taiex["date"].min(), end=df_taiex["date"].max(), freq="D")
    holidays = [d.strftime("%Y-%m-%d") for d in all_days if d not in set(df_taiex["date"])]

    figs = _build_six_charts(df_hl, df_taiex, df_margin, df_cnn,
                              df_taiex_macd, df_tpex_macd,
                              holidays, default_start, end_date)
    summary = _build_summary_html(df_hl, df_taiex, df_margin, df_cnn, df_taiex_macd, df_tpex_macd)
    _write_html(figs, end_date, summary)


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



def _write_html(figs: list, end_date: str, summary_html: str = ""):
    # 第一張圖帶入 plotlyjs CDN，其餘不重複載入
    divs = []
    for i, (fig, pid) in enumerate(zip(figs, ALL_PLOT_IDS)):
        divs.append(fig.to_html(
            full_html=False,
            include_plotlyjs="cdn" if i == 0 else False,
            div_id=pid,
        ))

    d0, d1, d2, d3, d4, d5, d6, d7 = divs

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>股市 Dashboard</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #fff; font-family: sans-serif; }}
    .container {{ max-width: 1600px; margin: 0 auto; padding: 16px 20px 40px; }}
    .summary-bar {{
      display: flex; flex-wrap: wrap; gap: 0;
      background: #f8f9fb; border: 1px solid #e2e8f0;
      border-radius: 8px; padding: 10px 8px; margin-bottom: 14px;
    }}
    .sc {{ flex:1; min-width:110px; padding:4px 16px; border-right:1px solid #dde3ed; }}
    .sc:last-child {{ border-right: none; }}
    .sc-label  {{ font-size:13px; color:#666; margin-bottom:2px; white-space:nowrap; }}
    .sc-value  {{ font-size:26px; font-weight:700; color:#222; line-height:1.2; }}
    .s-delta   {{ font-size:13px; margin-top:2px; display:block; }}
    .s-delta.up {{ color:#16a34a; }}
    .s-delta.dn {{ color:#dc2626; }}
    .s-delta.neutral {{ color:#888; }}

    /* ── Tab 導覽 ── */
    .tab-nav {{
      display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 16px;
      border-bottom: 2px solid #e2e8f0; padding-bottom: 0;
    }}
    .tab-btn {{
      padding: 10px 24px; font-size: 15px; font-weight: 600;
      border: none; border-radius: 8px 8px 0 0;
      background: transparent; color: #888;
      cursor: pointer; transition: all 0.15s;
      border-bottom: 3px solid transparent; margin-bottom: -2px;
    }}
    .tab-btn:hover {{ color: #2979c8; background: #f0f6ff; }}
    .tab-btn.active {{ color: #2979c8; border-bottom-color: #2979c8; background: #f0f6ff; }}

    /* ── Tab 內容 ── */
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}

    /* ── 圖表 tab 工具列 ── */
    .btn-group {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; }}
    .btn-group button {{
      padding:8px 20px; font-size:15px; border:2px solid #2979c8;
      border-radius:20px; background:#fff; color:#2979c8;
      cursor:pointer; transition:all 0.15s; font-weight:500;
    }}
    .btn-group button:hover {{ background:#e8f0fb; }}
    .btn-group button.active {{ background:#2979c8; color:#fff; }}
    .col-btn {{
      padding:8px 14px; font-size:15px; border:2px solid #888;
      border-radius:20px; background:#fff; color:#555;
      cursor:pointer; transition:all 0.15s; font-weight:500;
    }}
    .col-btn:hover {{ background:#f0f0f0; }}
    .col-btn.active {{ background:#555; color:#fff; border-color:#555; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:0px 12px; }}
    .grid.one-col {{ grid-template-columns:1fr; }}
    @media (max-width: 1024px) {{ .grid {{ grid-template-columns:1fr !important; }} }}
    @media (max-width: 600px) {{
      .sc {{ flex: 0 0 calc(50% - 2px); min-width: 0; }}
      .sc-value {{ font-size: 20px; }}
      .tab-btn {{ padding: 8px 12px; font-size: 13px; }}
      .btn-group button {{ padding: 6px 14px; font-size: 13px; }}
      .container {{ padding: 10px 12px 32px; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    {summary_html}

    <!-- Tab 導覽 -->
    <div class="tab-nav">
      <button class="tab-btn active" onclick="showTab('charts', this)">📊 圖表</button>
      <button class="tab-btn"        onclick="showTab('events', this)">📅 歷史事件</button>
      <button class="tab-btn"        onclick="showTab('cycles', this)">📈 景氣循環</button>
    </div>

    <!-- 圖表 tab -->
    <div id="tab-charts" class="tab-content active">
      <div style="display:flex; align-items:center; gap:16px; margin-bottom:16px; flex-wrap:wrap;">
      <div class="btn-group" style="margin-bottom:0;">
        <button onclick="setRange(0.5, this)" class="active">半年</button>
        <button onclick="setRange(1,   this)">1 年</button>
        <button onclick="setRange(3,   this)">3 年</button>
        <button onclick="setRange(5,   this)">5 年</button>
        <button onclick="setRange(10,  this)">10 年</button>
        <button onclick="setAll(this)">全部</button>
      </div>
      <div style="display:flex; gap:6px;">
        <button class="col-btn active" onclick="setCols(2, this)">▦ 2欄</button>
        <button class="col-btn"        onclick="setCols(1, this)">▤ 1欄</button>
      </div>
      </div>
      <div class="grid" id="main-grid">
        <div>{d0}</div>
        <div>{d1}</div>
        <div>{d2}</div>
        <div>{d3}</div>
        <div>{d4}</div>
        <div>{d5}</div>
        <div>{d6}</div>
        <div>{d7}</div>
      </div>
    </div>

    <!-- 歷史事件 tab -->
    <div id="tab-events" class="tab-content">
      <div id="events-content" class="md-content" style="text-align:center; padding:40px; color:#aaa;">載入中...</div>
    </div>

    <!-- 景氣循環 tab -->
    <div id="tab-cycles" class="tab-content">
      <div id="cycles-content" class="md-content" style="text-align:center; padding:40px; color:#aaa;">載入中...</div>
    </div>
  </div>

  <script>
    const ALL_PLOTS  = {str(ALL_PLOT_IDS).replace("'", '"')};
    const MAIN_PLOTS = ["plot-low", "plot-high", "plot-margin", "plot-cnn"];
    const MACD_PLOTS = ["plot-macd-taiex-w", "plot-macd-taiex-c", "plot-macd-tpex-w", "plot-macd-tpex-c"];

    function showTab(name, btn) {{
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('tab-' + name).classList.add('active');
      btn.classList.add('active');
      if (name === 'charts') {{
        ALL_PLOTS.forEach(id => Plotly.Plots.resize(document.getElementById(id)));
      }} else if (name === 'events') {{
        loadEvents();
      }} else if (name === 'cycles') {{
        loadCycles();
      }}
    }}

    // ── 歷史事件：fetch MD → marked.js 渲染 ─────────────────
    async function loadEvents() {{
      const el = document.getElementById('events-content');
      if (el.dataset.loaded) return;
      try {{
        const resp = await fetch('taiwan_market_events.md');
        if (!resp.ok) throw new Error('not found');
        const text = await resp.text();
        marked.setOptions({{ gfm: true, breaks: true }});
        el.innerHTML = marked.parse(text);
        el.dataset.loaded = '1';
      }} catch(e) {{
        el.innerHTML = `<div style="text-align:center; padding:60px; color:#888;">
          <div style="font-size:48px; margin-bottom:16px;">📅</div>
          <div style="font-size:18px; font-weight:600;">找不到 taiwan_market_events.md</div>
        </div>`;
      }}
    }}

    async function loadCycles() {{
      const el = document.getElementById('cycles-content');
      if (el.dataset.loaded) return;
      try {{
        const resp = await fetch('taiwan_market_cycles.md');
        if (!resp.ok) throw new Error('not found');
        const text = await resp.text();
        marked.setOptions({{ gfm: true, breaks: true }});
        el.innerHTML = marked.parse(text);
        el.dataset.loaded = '1';
      }} catch(e) {{
        el.innerHTML = `<div style="text-align:center; padding:60px; color:#888;">
          <div style="font-size:48px; margin-bottom:16px;">📈</div>
          <div style="font-size:18px; font-weight:600;">找不到 taiwan_market_cycles.md</div>
        </div>`;
      }}
    }}


    function resetAxes(id) {{
      const el = document.getElementById(id);
      const btn = el.querySelector('.modebar-btn[data-title="Reset axes"]');
      if (btn) btn.click();
      return el;
    }}

    function setRange(years, btn) {{
      btn.closest('.btn-group').querySelectorAll('button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const end = new Date('{end_date}');
      end.setDate(end.getDate() + 1);
      const start = new Date(end);
      const months = Math.round(years * 12);
      start.setMonth(start.getMonth() - months);
      const fmt = d => d.toISOString().split('T')[0];
      MAIN_PLOTS.forEach(id => {{
        const el = resetAxes(id);
        Plotly.relayout(el, {{'xaxis.autorange': false, 'xaxis.range': [fmt(start), fmt(end)]}});
      }});
      MACD_PLOTS.forEach(id => {{
        Plotly.relayout(document.getElementById(id), {{
          'xaxis.autorange': false, 'xaxis.range': [fmt(start), fmt(end)],
          'xaxis2.autorange': false, 'xaxis2.range': [fmt(start), fmt(end)]
        }});
      }});
    }}

    function setAll(btn) {{
      btn.closest('.btn-group').querySelectorAll('button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      MAIN_PLOTS.forEach(id => {{
        Plotly.relayout(document.getElementById(id), {{'xaxis.autorange': true}});
      }});
      MACD_PLOTS.forEach(id => {{
        Plotly.relayout(document.getElementById(id), {{
          'xaxis.autorange': true, 'yaxis.autorange': true,
          'yaxis2.autorange': true, 'yaxis3.autorange': true
        }});
      }});
    }}

    function setCols(n, btn) {{
      btn.closest('div').querySelectorAll('.col-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const grid = document.getElementById('main-grid');
      grid.classList.toggle('one-col', n === 1);
      ALL_PLOTS.forEach(id => Plotly.Plots.resize(document.getElementById(id)));
    }}

    window.addEventListener('resize', () => {{
      ALL_PLOTS.forEach(id => Plotly.Plots.resize(document.getElementById(id)));
    }});
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
      border-bottom: 2px solid #2979c8; padding-bottom: 8px; margin-top: 32px; margin-bottom: 16px;
      text-align: left;
    }}
    .md-content h2 {{
      font-size: 20px; font-weight: 700; color: #1a1a1a;
      border-left: 4px solid #2979c8; padding-left: 12px;
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
      border-left: 4px solid #2979c8; margin: 12px 0;
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
    default_start = (today - relativedelta(years=3)).strftime("%Y-%m-%d")

    df_hl, df_taiex, df_margin, df_cnn = _mock_main_data()
    df_taiex_macd = _mock_macd_data()
    df_tpex_macd  = _mock_macd_data()

    figs = _build_six_charts(df_hl, df_taiex, df_margin, df_cnn,
                              df_taiex_macd, df_tpex_macd,
                              holidays=[], default_start=default_start, end_date=end_date)
    summary = _build_summary_html(df_hl, df_taiex, df_margin, df_cnn, df_taiex_macd, df_tpex_macd)
    _write_html(figs, end_date, summary)


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        run_dashboard_test()
    else:
        run_dashboard()
