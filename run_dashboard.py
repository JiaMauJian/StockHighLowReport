import os
import sqlite3
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
OUTPUT_FILE = "dashboard.html"
MACD_START_DATE = "1990-01-01"


def _read_token():
    token = os.environ.get("FINMIND_TOKEN")
    if not token:
        raise RuntimeError("❌ 找不到 FINMIND_TOKEN，請確認 .env 檔案已設定。")
    return token


def _load_high_low(conn, start_date: str) -> pd.DataFrame:
    fetch_from = (
        pd.Timestamp(start_date) - pd.Timedelta(days=DAYS + 10)
    ).strftime("%Y-%m-%d")

    df_all = pd.read_sql_query(
        f"SELECT date, stock_id, close FROM stock_daily"
        f" WHERE date >= '{fetch_from}'"
        f" ORDER BY stock_id, date",
        conn,
    )
    df_all["date"] = pd.to_datetime(df_all["date"], errors="coerce")
    df_all = df_all.dropna(subset=["date"])

    df_total = (
        df_all.groupby("date")["stock_id"]
        .nunique()
        .reset_index(name="num_traded_stocks")
    )

    df_idx = df_all.set_index("date")
    df_idx["min_close"] = df_idx.groupby("stock_id")["close"].transform(
        lambda x: x.rolling(f"{DAYS}D", min_periods=1).min()
    )
    df_idx["max_close"] = df_idx.groupby("stock_id")["close"].transform(
        lambda x: x.rolling(f"{DAYS}D", min_periods=1).max()
    )
    df_stats = df_idx.reset_index()

    df_lows = (
        df_stats[df_stats["close"] == df_stats["min_close"]]
        .groupby("date")["stock_id"]
        .nunique()
        .reset_index(name="num_lows")
    )
    df_highs = (
        df_stats[df_stats["close"] == df_stats["max_close"]]
        .groupby("date")["stock_id"]
        .nunique()
        .reset_index(name="num_highs")
    )

    df = (
        df_total.merge(df_lows, on="date", how="left")
        .merge(df_highs, on="date", how="left")
    )
    df[["num_lows", "num_highs"]] = df[["num_lows", "num_highs"]].fillna(0).astype(int)
    df["low_ratio"] = (df["num_lows"] * 100.0 / df["num_traded_stocks"]).round(2)
    df["high_ratio"] = (df["num_highs"] * 100.0 / df["num_traded_stocks"]).round(2)

    df = df[df["date"] >= pd.Timestamp(start_date)].sort_values("date").reset_index(drop=True)
    return df


def _load_taiex(conn, start_date: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        f"SELECT date, close FROM stock_daily"
        f" WHERE stock_id='TAIEX' AND date >= '{start_date}'"
        f" ORDER BY date",
        conn,
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_margin(api, start_date: str, end_date: str) -> pd.DataFrame:
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


def _build_macd_fig(df: pd.DataFrame, title: str) -> go.Figure:
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.5, 0.25, 0.25],
        subplot_titles=[
            f"{title} K線",
            f"{title} MACD (H+L+2C)/4（三竹/玩股）",
            f"{title} MACD close（鉅亨）",
        ],
        vertical_spacing=0.09,
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
    for row, suffix, label in [(2, "w", "(H+L+2C)/4"), (3, "c", "close")]:
        colors = ["red" if v >= 0 else "green" for v in df[f"hist_{suffix}"]]
        fig.add_trace(
            go.Bar(x=df["date"], y=df[f"hist_{suffix}"],
                   name=f"OSC {label}", marker_color=colors, opacity=0.5,
                   showlegend=False, hovertemplate="<extra></extra>"),
            row=row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df["date"], y=df[f"macd_{suffix}"],
                       name=f"DIF {label}", line=dict(color="blue", width=1),
                       showlegend=False, hovertemplate="<extra></extra>"),
            row=row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df["date"], y=df[f"signal_{suffix}"],
                       name=f"MACD9 {label}", line=dict(color="orange", width=1),
                       showlegend=False, hovertemplate="<extra></extra>"),
            row=row, col=1,
        )
    default_3yr = (date.today() - relativedelta(years=3)).strftime("%Y-%m")
    end_ym      = date.today().strftime("%Y-%m")
    fig.update_layout(
        height=1200,
        autosize=True,
        template="plotly_white",
        showlegend=False,
        hoverlabel=dict(font_size=13),
        hovermode="x",
        xaxis_rangeslider_visible=False,
        xaxis2_rangeslider_visible=False,
        xaxis3_rangeslider_visible=False,
    )
    fig.update_xaxes(
        range=[default_3yr, end_ym],
        showticklabels=True,
        tickformat="%Y-%m",
        showspikes=True,
        spikesnap="cursor",
        spikemode="across",
        spikethickness=1,
        spikecolor="#888",
        spikedash="dot",
    )
    fig.update_traces(xhoverformat="%Y-%m")
    return fig


def run_dashboard():
    token = _read_token()

    today = date.today()
    end_date = today.strftime("%Y-%m-%d")
    start_date = (today - relativedelta(years=MAX_YEARS)).strftime("%Y-%m-%d")

    print("讀取 stock.db 資料...")
    conn = sqlite3.connect("stock.db")
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        df_hl = _load_high_low(conn, start_date)
        df_taiex = _load_taiex(conn, start_date)
    finally:
        conn.close()
    print("stock.db 資料讀取完成")

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
    all_days = pd.date_range(start=df_taiex["date"].min(),
                             end=df_taiex["date"].max(), freq="D")
    trading_days = set(df_taiex["date"])
    holidays = [d.strftime("%Y-%m-%d") for d in all_days if d not in trading_days]

    # 預設顯示最近 3 年
    default_start = (today - relativedelta(years=3)).strftime("%Y-%m-%d")

    # ── 建立圖表 ──────────────────────────────────────────────
    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        subplot_titles=[
            "TAIEX vs 融資維持率",
            f"股價創{DAYS}日新低比例",
            f"股價創{DAYS}日新高比例",
            "TAIEX vs CNN Fear/Greed",
        ],
        specs=[
            [{"secondary_y": True}],
            [{"secondary_y": True}],
            [{"secondary_y": True}],
            [{"secondary_y": True}],
        ],
        vertical_spacing=0.06,
    )

    # 圖1：TAIEX + 融資維持率
    fig.add_trace(
        go.Scatter(x=df_taiex["date"], y=df_taiex["close"],
                   name="TAIEX", line=dict(color="black")),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df_margin["date"], y=df_margin["TotalExchangeMarginMaintenance"],
                   name="融資維持率", line=dict(color="red")),
        row=1, col=1, secondary_y=True,
    )

    # 圖2：新低比例（紅柱）+ TAIEX 收盤（黑線）
    fig.add_trace(
        go.Bar(x=df_hl["date"], y=df_hl["low_ratio"],
               name=f"股價低於{DAYS}日比例(%)", marker_color="red", opacity=0.8),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df_taiex["date"], y=df_taiex["close"],
                   name="收盤價", line=dict(color="black"), showlegend=False),
        row=2, col=1, secondary_y=True,
    )

    # 圖3：新高比例（綠柱）+ TAIEX 收盤（黑線）
    fig.add_trace(
        go.Bar(x=df_hl["date"], y=df_hl["high_ratio"],
               name=f"股價高於{DAYS}日比例(%)", marker_color="green", opacity=0.8),
        row=3, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df_taiex["date"], y=df_taiex["close"],
                   name="收盤價", line=dict(color="black"), showlegend=False),
        row=3, col=1, secondary_y=True,
    )

    # 圖4：TAIEX + CNN Fear/Greed
    fig.add_trace(
        go.Scatter(x=df_taiex["date"], y=df_taiex["close"],
                   name="TAIEX", line=dict(color="black"), showlegend=False),
        row=4, col=1,
    )
    fig.add_trace(
        go.Scatter(x=df_cnn["date"], y=df_cnn["fear_greed"],
                   name="CNN Fear/Greed", line=dict(color="orange")),
        row=4, col=1, secondary_y=True,
    )

    fig.update_yaxes(title_text="TAIEX", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="融資維持率", row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="比例 (%)", row=2, col=1, secondary_y=False, range=[0, 100])
    fig.update_yaxes(title_text="TAIEX", row=2, col=1, secondary_y=True)
    fig.update_yaxes(title_text="比例 (%)", row=3, col=1, secondary_y=False, range=[0, 100])
    fig.update_yaxes(title_text="TAIEX", row=3, col=1, secondary_y=True)
    fig.update_yaxes(title_text="TAIEX", row=4, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Fear/Greed", row=4, col=1, secondary_y=True, range=[0, 100])

    fig.update_xaxes(rangeslider=dict(visible=False), showticklabels=True,
                     showline=True, linecolor="#444", mirror=True,
                     rangebreaks=[dict(values=holidays)],
                     tickformat="%Y-%m-%d")
    fig.update_yaxes(showline=True, linecolor="#444", mirror=True)

    # 預設顯示最近 3 年
    fig.update_xaxes(range=[default_start, end_date])

    fig.update_layout(
        height=1800,
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hoverlabel=dict(font_size=13),
        hovermode="x unified",
    )
    fig.update_traces(xhoverformat="%Y-%m-%d")

    fig_taiex_macd = _build_macd_fig(df_taiex_macd, "TAIEX 上市")
    fig_tpex_macd  = _build_macd_fig(df_tpex_macd,  "TPEx 上櫃")

    chart_div      = fig.to_html(full_html=False, include_plotlyjs="cdn", div_id="dashboard")
    macd_taiex_div = fig_taiex_macd.to_html(full_html=False, include_plotlyjs=False, div_id="macd_taiex")
    macd_tpex_div  = fig_tpex_macd.to_html(full_html=False, include_plotlyjs=False, div_id="macd_tpex")

    _write_html(chart_div, macd_taiex_div, macd_tpex_div, end_date)


def _write_html(chart_div: str, macd_taiex_div: str, macd_tpex_div: str, end_date: str):
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>股市 Dashboard</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #fff; font-family: sans-serif; }}
    .container {{ max-width: 1400px; margin: 0 auto; padding: 0 20px 20px; }}
    .tabs {{
      display: flex;
      border-bottom: 2px solid #d0d7e3;
      margin-bottom: 16px;
      padding-top: 16px;
    }}
    .tab-btn {{
      padding: 10px 28px;
      font-size: 15px;
      font-weight: 500;
      border: none;
      background: transparent;
      cursor: pointer;
      color: #555;
      border-bottom: 3px solid transparent;
      margin-bottom: -2px;
      transition: all 0.15s;
    }}
    .tab-btn:hover {{ color: #2979c8; }}
    .tab-btn.active {{
      color: #2979c8;
      border-bottom-color: #2979c8;
    }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .btn-group {{
      display: flex;
      gap: 8px;
      margin-bottom: 16px;
    }}
    .btn-group button {{
      padding: 8px 20px;
      font-size: 15px;
      border: 2px solid #2979c8;
      border-radius: 20px;
      background: #fff;
      color: #2979c8;
      cursor: pointer;
      transition: all 0.15s;
      font-weight: 500;
    }}
    .btn-group button:hover {{ background: #e8f0fb; }}
    .btn-group button.active {{
      background: #2979c8;
      color: #fff;
    }}
    .macd-wrapper {{ position: relative; }}
    .macd-info-ov {{
      position: absolute;
      z-index: 10;
      display: flex;
      align-items: center;
      gap: 16px;
      font-size: 18px;
      font-family: monospace;
      background: rgba(255,255,255,0.88);
      padding: 2px 8px;
      pointer-events: none;
      white-space: nowrap;
    }}
    .mi-label {{ color: #555; font-weight: 600; margin-right: 4px; }}
    .mi-m9    {{ color: #e2843a; font-weight: bold; }}
    .mi-dif   {{ color: #2255cc; font-weight: bold; }}
    .mi-osc   {{ font-weight: bold; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="tabs">
      <button class="tab-btn active" onclick="showTab('main', this)">大盤指標</button>
      <button class="tab-btn"        onclick="showTab('macd_taiex_tab', this)">TAIEX 月MACD</button>
      <button class="tab-btn"        onclick="showTab('macd_tpex_tab', this)">TPEx 月MACD</button>
    </div>

    <div id="main" class="tab-panel active">
      <div class="btn-group">
        <button onclick="setRange(1,  'dashboard', this)">1 年</button>
        <button onclick="setRange(3,  'dashboard', this)" class="active">3 年</button>
        <button onclick="setRange(5,  'dashboard', this)">5 年</button>
        <button onclick="setRange(10, 'dashboard', this)">10 年</button>
      </div>
      {chart_div}
    </div>

    <div id="macd_taiex_tab" class="tab-panel">
      <div class="btn-group">
        <button onclick="setRange(1,  'macd_taiex', this)">1 年</button>
        <button onclick="setRange(3,  'macd_taiex', this)" class="active">3 年</button>
        <button onclick="setRange(5,  'macd_taiex', this)">5 年</button>
        <button onclick="setRange(10, 'macd_taiex', this)">10 年</button>
      </div>
      <div class="macd-wrapper">
        {macd_taiex_div}
        <div id="taiex_iw" class="macd-info-ov">
          <span class="mi-label">MACD (H+L+2C)/4</span>
          <span class="mi-m9"  id="taiex_iw_m9">MACD9 --</span>
          <span class="mi-dif" id="taiex_iw_dif">DIF --</span>
          <span class="mi-osc" id="taiex_iw_osc">OSC --</span>
        </div>
        <div id="taiex_ic" class="macd-info-ov">
          <span class="mi-label">MACD close</span>
          <span class="mi-m9"  id="taiex_ic_m9">MACD9 --</span>
          <span class="mi-dif" id="taiex_ic_dif">DIF --</span>
          <span class="mi-osc" id="taiex_ic_osc">OSC --</span>
        </div>
      </div>
    </div>

    <div id="macd_tpex_tab" class="tab-panel">
      <div class="btn-group">
        <button onclick="setRange(1,  'macd_tpex', this)">1 年</button>
        <button onclick="setRange(3,  'macd_tpex', this)" class="active">3 年</button>
        <button onclick="setRange(5,  'macd_tpex', this)">5 年</button>
        <button onclick="setRange(10, 'macd_tpex', this)">10 年</button>
      </div>
      <div class="macd-wrapper">
        {macd_tpex_div}
        <div id="tpex_iw" class="macd-info-ov">
          <span class="mi-label">MACD (H+L+2C)/4</span>
          <span class="mi-m9"  id="tpex_iw_m9">MACD9 --</span>
          <span class="mi-dif" id="tpex_iw_dif">DIF --</span>
          <span class="mi-osc" id="tpex_iw_osc">OSC --</span>
        </div>
        <div id="tpex_ic" class="macd-info-ov">
          <span class="mi-label">MACD close</span>
          <span class="mi-m9"  id="tpex_ic_m9">MACD9 --</span>
          <span class="mi-dif" id="tpex_ic_dif">DIF --</span>
          <span class="mi-osc" id="tpex_ic_osc">OSC --</span>
        </div>
      </div>
    </div>
  </div>

  <script>
    function showTab(id, btn) {{
      document.querySelectorAll('.tab-panel').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById(id).classList.add('active');
      btn.classList.add('active');
      document.getElementById(id).querySelectorAll('.plotly-graph-div').forEach(el => Plotly.Plots.resize(el));
    }}

    function setRange(years, plotId, btn) {{
      btn.closest('.btn-group').querySelectorAll('button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const plot = document.getElementById(plotId);
      const end = new Date('{end_date}');
      end.setDate(end.getDate() + 1);
      const start = new Date(end);
      start.setFullYear(start.getFullYear() - years);
      const fmt = d => d.toISOString().split('T')[0];
      Plotly.relayout(plot, {{'xaxis.range': [fmt(start), fmt(end)]}});
    }}

    function fmtM(v) {{
      if (v == null) return '--';
      return v.toFixed(2) + (v >= 0 ? '↑' : '↓');
    }}

    function setOsc(id, v) {{
      const e = document.getElementById(id);
      if (!e) return;
      e.textContent = 'OSC ' + fmtM(v);
      e.style.color = (v != null && v >= 0) ? '#cc0000' : '#009900';
    }}

    function updateMacdInfo(pfx, pts) {{
      const get = n => {{ const p = pts.find(p => p.data && p.data.name && p.data.name.startsWith(n)); return p ? p.y : null; }};
      const set = (id, t) => {{ const e = document.getElementById(id); if (e) e.textContent = t; }};
      set(pfx+'_iw_m9',  'MACD9 ' + fmtM(get('MACD9 (')));
      set(pfx+'_iw_dif', 'DIF '   + fmtM(get('DIF (')));
      setOsc(pfx+'_iw_osc', get('OSC ('));
      set(pfx+'_ic_m9',  'MACD9 ' + fmtM(get('MACD9 c')));
      set(pfx+'_ic_dif', 'DIF '   + fmtM(get('DIF c')));
      setOsc(pfx+'_ic_osc', get('OSC c'));
    }}

    function positionMacdInfo(plotId, pfx) {{
      const plot = document.getElementById(plotId);
      if (!plot._fullLayout || !plot._fullLayout.yaxis2) return;
      const l  = plot._fullLayout;
      const pH = l.height - l.margin.t - l.margin.b;
      const y2px = l.margin.t + pH * (1 - l.yaxis2.domain[1]);
      const y3px = l.margin.t + pH * (1 - l.yaxis3.domain[1]);
      const iw = document.getElementById(pfx+'_iw');
      const ic = document.getElementById(pfx+'_ic');
      if (iw) {{ iw.style.top = (y2px+4)+'px'; iw.style.left = (l.margin.l+4)+'px'; }}
      if (ic) {{ ic.style.top = (y3px+4)+'px'; ic.style.left = (l.margin.l+4)+'px'; }}
    }}

    function initMacdInfo(plotId, pfx) {{
      const plot = document.getElementById(plotId);
      if (!plot || !plot.data) return;
      const last = plot.data.map(t => ({{ data: t, y: t.y && t.y.length ? t.y[t.y.length-1] : null }})).filter(p => p.y != null);
      updateMacdInfo(pfx, last);
      positionMacdInfo(plotId, pfx);
      plot.on('plotly_hover',    ev => updateMacdInfo(pfx, ev.points));
      plot.on('plotly_unhover',  ()  => updateMacdInfo(pfx, last));
      plot.on('plotly_relayout', ()  => positionMacdInfo(plotId, pfx));
    }}

    document.addEventListener('DOMContentLoaded', function() {{
      initMacdInfo('macd_taiex', 'taiex');
      initMacdInfo('macd_tpex',  'tpex');
    }});
  </script>
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
    # GBM: 月報酬率 N(0, 5%)，產生有漲有跌的走勢讓 MACD 自然震盪
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


def run_dashboard_test():
    today     = date.today()
    end_date  = today.strftime("%Y-%m-%d")

    df_taiex_macd = _mock_macd_data()
    df_tpex_macd  = _mock_macd_data()

    fig_taiex_macd = _build_macd_fig(df_taiex_macd, "TAIEX 上市")
    fig_tpex_macd  = _build_macd_fig(df_tpex_macd,  "TPEx 上櫃")

    placeholder = (
        '<div id="dashboard" style="height:300px;display:flex;align-items:center;'
        'justify-content:center;color:#aaa;font-size:18px;border:2px dashed #ccc;">'
        '（測試模式：大盤指標略過）</div>'
    )
    # include_plotlyjs="cdn" goes into the placeholder so the MACD divs can reuse Plotly
    chart_div = (
        '<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>'
        + placeholder
    )
    macd_taiex_div = fig_taiex_macd.to_html(full_html=False, include_plotlyjs=False, div_id="macd_taiex")
    macd_tpex_div  = fig_tpex_macd.to_html(full_html=False, include_plotlyjs=False, div_id="macd_tpex")

    _write_html(chart_div, macd_taiex_div, macd_tpex_div, end_date)


if __name__ == "__main__":
    run_dashboard_test()
