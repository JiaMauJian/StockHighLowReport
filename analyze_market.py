"""
analyze_market.py
─────────────────
抓取 20 年台股市場溫度指標，計算各指標歷史百分位，
透過 Claude API 產生「過熱 / 中性 / 過冷」分析報告。

執行方式：
    python analyze_market.py

環境變數（.env）：
    FINMIND_TOKEN         FinMind API token
    TURSO_DATABASE_URL    Turso libsql:// URL
    TURSO_AUTH_TOKEN      Turso JWT token
    ANTHROPIC_API_KEY     Anthropic API key
"""

import os
import sys
from datetime import date

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from FinMind.data import DataLoader

load_dotenv()

MACD_START_DATE = "1990-01-01"
HISTORY_YEARS   = 20          # 計算百分位用的歷史長度


# ── 資料載入 ────────────────────────────────────────────────────

def _load_turso_all() -> pd.DataFrame:
    """從 Turso 取得所有 high_low_60d 資料（不限日期）。"""
    url   = os.environ.get("TURSO_DATABASE_URL", "").replace("libsql://", "https://")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not url or not token:
        raise RuntimeError("❌ 找不到 TURSO_DATABASE_URL 或 TURSO_AUTH_TOKEN")

    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": (
                        "SELECT date, taiex_close, low_ratio, high_ratio "
                        "FROM high_low_60d ORDER BY date"
                    ),
                    "args": [],
                },
            },
            {"type": "close"},
        ]
    }
    r = requests.post(
        f"{url}/v2/pipeline",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    result = r.json()["results"][0]["response"]["result"]
    cols = [c["name"] for c in result["cols"]]
    data = [[cell.get("value") for cell in row] for row in result["rows"]]
    df = pd.DataFrame(data, columns=cols)
    df["date"]        = pd.to_datetime(df["date"])
    df["taiex_close"] = pd.to_numeric(df["taiex_close"])
    df["low_ratio"]   = pd.to_numeric(df["low_ratio"])
    df["high_ratio"]  = pd.to_numeric(df["high_ratio"])
    return df


def _load_margin(api, start_date: str) -> pd.DataFrame:
    df = api.taiwan_total_exchange_margin_maintenance(
        start_date=start_date, end_date=date.today().strftime("%Y-%m-%d")
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_cnn(token: str, start_date: str) -> pd.DataFrame:
    res = requests.get(
        "https://api.finmindtrade.com/api/v4/data",
        params={
            "dataset": "CnnFearGreedIndex",
            "start_date": start_date,
            "end_date": date.today().strftime("%Y-%m-%d"),
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    df = pd.DataFrame(res.json()["data"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def _calc_macd(series: pd.Series):
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


# ── 統計工具 ────────────────────────────────────────────────────

def _pct_rank(series: pd.Series, value: float) -> float:
    """value 在 series 中的歷史百分位（0~100）。"""
    arr = series.dropna().values
    if len(arr) == 0:
        return 50.0
    return float((arr < value).sum() / len(arr) * 100)


def _stat(name: str, series: pd.Series, curr: float, reverse: bool = False) -> dict:
    """
    建立單一指標統計摘要。
    reverse=True  → 指標數值愈低代表市場愈恐懼（如融資維持率、60日新低反轉）。
    sentiment_pct：0 = 極度恐懼，100 = 極度貪婪。
    """
    s = series.dropna()
    pct = _pct_rank(s, curr)
    return {
        "name":          name,
        "current":       curr,
        "percentile":    pct,
        "sentiment_pct": (100 - pct) if reverse else pct,
        "min":    float(s.min()),
        "max":    float(s.max()),
        "median": float(s.median()),
        "p10":    float(s.quantile(0.10)),
        "p25":    float(s.quantile(0.25)),
        "p75":    float(s.quantile(0.75)),
        "p90":    float(s.quantile(0.90)),
        "n":      int(s.count()),
    }


# ── 報告產生 ────────────────────────────────────────────────────

def _build_prompt(stats: list, latest_date: str, taiex: float) -> str:
    lines = [
        f"今日日期：{date.today().strftime('%Y-%m-%d')}",
        f"最新資料日期：{latest_date}",
        f"加權指數收盤：{taiex:,.0f}",
        "",
        f"以下是台股各市場溫度指標的當前數值與歷史統計（歷史樣本最多追溯 {HISTORY_YEARS} 年）：",
        "",
    ]

    for s in stats:
        lines.append(
            f"【{s['name']}】"
            f"  當前值：{s['current']:.2f}"
            f"  |  歷史百分位：{s['percentile']:.1f}%"
            f"  |  歷史範圍：{s['min']:.2f} ~ {s['max']:.2f}"
            f"  |  中位數：{s['median']:.2f}"
            f"  |  P10/P25/P75/P90：{s['p10']:.2f}/{s['p25']:.2f}/{s['p75']:.2f}/{s['p90']:.2f}"
            f"  |  樣本數：{s['n']} 筆"
        )
        lines.append(
            f"   ↳ 市場溫度分數（0=極度恐懼，100=極度貪婪）：{s['sentiment_pct']:.1f}"
        )
        lines.append("")

    avg = sum(s["sentiment_pct"] for s in stats) / len(stats)
    lines.append(f"📊 各指標平均市場溫度分數：{avg:.1f} / 100")
    lines.append("")
    lines.append(
        "請根據以上資料，以繁體中文撰寫一份市場分析報告，"
        "分析目前台股市場是處於「過熱（貪婪）」、「中性」還是「過冷（恐懼）」狀態。\n\n"
        "報告格式如下：\n"
        "1. 整體市場溫度判斷（一句話結論，含溫度分數區間說明）\n"
        "2. 各指標解讀（每個指標 2~3 句話，說明當前位置相較歷史的意義）\n"
        "3. 綜合觀察與風險提示\n"
        "4. 根據歷史數據，類似水位時市場後續的可能走向\n\n"
        "語氣客觀，勿給予明確買賣建議。"
    )
    return "\n".join(lines)


def _print_summary(stats: list):
    print("\n" + "=" * 65)
    print("  📊 指標摘要")
    print("=" * 65)
    for s in stats:
        bar_len = int(s["sentiment_pct"] / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(
            f"  {s['name']:<28}"
            f"  當前 {s['current']:8.2f}"
            f"  百分位 {s['percentile']:5.1f}%"
            f"  溫度 [{bar}] {s['sentiment_pct']:5.1f}"
        )
    avg = sum(s["sentiment_pct"] for s in stats) / len(stats)
    bar_len = int(avg / 5)
    bar = "█" * bar_len + "░" * (20 - bar_len)
    print("-" * 65)
    print(f"  {'平均市場溫度':<28}  {'':>14}  溫度 [{bar}] {avg:5.1f}")
    if avg >= 75:
        label = "⚠️  過熱（貪婪）"
    elif avg >= 55:
        label = "🟡 偏熱"
    elif avg >= 45:
        label = "🟢 中性"
    elif avg >= 25:
        label = "🔵 偏冷"
    else:
        label = "❄️  過冷（恐懼）"
    print(f"\n  市場溫度判斷：{label}  ({avg:.1f}/100)")
    print("=" * 65 + "\n")


# ── 主程式 ──────────────────────────────────────────────────────

def main():
    finmind_token  = os.environ.get("FINMIND_TOKEN", "")
    anthropic_key  = os.environ.get("ANTHROPIC_API_KEY", "")
    if not finmind_token:
        print("❌ 找不到 FINMIND_TOKEN"); sys.exit(1)
    use_api = bool(anthropic_key)

    start_20yr = (date.today() - relativedelta(years=HISTORY_YEARS)).strftime("%Y-%m-%d")

    api = DataLoader()
    api.login_by_token(api_token=finmind_token)

    # ── 載入資料 ──
    print("📡 載入 Turso high_low_60d（全部）...")
    df_hl = _load_turso_all()

    print("📡 載入融資維持率（20 年）...")
    df_margin = _load_margin(api, start_20yr)

    print("📡 載入 CNN Fear & Greed（20 年）...")
    df_cnn = _load_cnn(finmind_token, start_20yr)

    print("📡 載入 TAIEX 月線 MACD（從 1990）...")
    df_taiex_macd = _fetch_monthly_macd(api, "TAIEX")

    print("📡 載入 TPEx 月線 MACD（從 1990）...")
    df_tpex_macd = _fetch_monthly_macd(api, "TPEx")

    # ── 取各指標最新值 ──
    df_hl_s      = df_hl.sort_values("date")
    df_margin_s  = df_margin.sort_values("date")
    df_cnn_s     = df_cnn.sort_values("date")
    df_taiex_m   = df_taiex_macd.sort_values("date")
    df_tpex_m    = df_tpex_macd.sort_values("date")

    latest_date  = df_hl_s["date"].iloc[-1].strftime("%Y-%m-%d")
    taiex_now    = float(df_hl_s["taiex_close"].iloc[-1])

    curr_low     = float(df_hl_s["low_ratio"].iloc[-1])
    curr_high    = float(df_hl_s["high_ratio"].iloc[-1])
    curr_margin  = float(df_margin_s["TotalExchangeMarginMaintenance"].iloc[-1])
    curr_cnn     = float(df_cnn_s["fear_greed"].iloc[-1])
    curr_tosc_c  = float(df_taiex_m["hist_c"].iloc[-1])
    curr_tosc_w  = float(df_taiex_m["hist_w"].iloc[-1])
    curr_posc_c  = float(df_tpex_m["hist_c"].iloc[-1])
    curr_posc_w  = float(df_tpex_m["hist_w"].iloc[-1])

    # ── 計算統計（百分位以全部可得歷史為基準）──
    stats = [
        # 60日新低比例高 → 更多股票創新低 → 恐懼，故 reverse=True（高值=恐懼）
        _stat("60日新低比例(%)",    df_hl_s["low_ratio"],                       curr_low,    reverse=True),
        # 60日新高比例高 → 更多股票創新高 → 貪婪
        _stat("60日新高比例(%)",    df_hl_s["high_ratio"],                      curr_high,   reverse=False),
        # 融資維持率低 → 融資壓力大 → 恐懼，故 reverse=True
        _stat("大盤融資維持率",      df_margin_s["TotalExchangeMarginMaintenance"], curr_margin, reverse=True),
        # CNN 0=恐懼 100=貪婪
        _stat("CNN Fear & Greed",   df_cnn_s["fear_greed"],                     curr_cnn,    reverse=False),
        # MACD OSC 正值=多頭 → 偏貪婪
        _stat("上市MACD OSC(close)", df_taiex_m["hist_c"],                      curr_tosc_c, reverse=False),
        _stat("上市MACD OSC(hlcc4)", df_taiex_m["hist_w"],                      curr_tosc_w, reverse=False),
        _stat("上櫃MACD OSC(close)", df_tpex_m["hist_c"],                       curr_posc_c, reverse=False),
        _stat("上櫃MACD OSC(hlcc4)", df_tpex_m["hist_w"],                       curr_posc_w, reverse=False),
    ]

    _print_summary(stats)

    # ── 匯出 20 年原始資料到 Excel ──
    reports_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    xlsx_path = os.path.join(reports_dir, f"market_data_{date.today().strftime('%Y%m%d')}.xlsx")
    print(f"\n💾 匯出資料至 {xlsx_path} ...")

    # 整理 MACD（只保留有意義的欄位）
    macd_cols = ["date", "open", "max", "min", "close",
                 "macd_w", "signal_w", "hist_w",
                 "macd_c", "signal_c", "hist_c"]

    # 只取 20 年內的 MACD 資料（MACD 從 1990 算，但輸出只保留近 20 年方便閱讀）
    cutoff = pd.Timestamp(date.today() - relativedelta(years=HISTORY_YEARS))
    df_taiex_export = df_taiex_m[df_taiex_m["date"].astype(str) >= cutoff.strftime("%Y-%m")][macd_cols].copy()
    df_tpex_export  = df_tpex_m [df_tpex_m ["date"].astype(str) >= cutoff.strftime("%Y-%m")][macd_cols].copy()

    # high_low 以全部可得資料為準（可能不足 20 年）
    df_hl_export = df_hl_s[["date", "taiex_close", "low_ratio", "high_ratio"]].copy()
    df_margin_export = df_margin_s[["date", "TotalExchangeMarginMaintenance"]].copy()
    df_cnn_export    = df_cnn_s[["date", "fear_greed"]].copy()

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df_hl_export.to_excel(    writer, sheet_name="60日新高新低",   index=False)
        df_margin_export.to_excel(writer, sheet_name="融資維持率",      index=False)
        df_cnn_export.to_excel(   writer, sheet_name="CNN_Fear_Greed",  index=False)
        df_taiex_export.to_excel( writer, sheet_name="TAIEX_月MACD",   index=False)
        df_tpex_export.to_excel(  writer, sheet_name="TPEx_月MACD",    index=False)

        # 指標摘要頁
        summary_rows = [{
            "指標": s["name"],
            "當前值": round(s["current"], 2),
            "歷史百分位(%)": round(s["percentile"], 1),
            "市場溫度分(0恐懼~100貪婪)": round(s["sentiment_pct"], 1),
            "歷史最小": round(s["min"], 2),
            "P10": round(s["p10"], 2),
            "P25": round(s["p25"], 2),
            "中位數": round(s["median"], 2),
            "P75": round(s["p75"], 2),
            "P90": round(s["p90"], 2),
            "歷史最大": round(s["max"], 2),
            "樣本數": s["n"],
        } for s in stats]
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="指標摘要", index=False)

    print(f"   ✅ 共 6 個工作表：60日新高新低 / 融資維持率 / CNN / TAIEX月MACD / TPEx月MACD / 指標摘要")

    # ── 建立 prompt ──
    prompt = _build_prompt(stats, latest_date, taiex_now)
    avg = sum(s["sentiment_pct"] for s in stats) / len(stats)

    # ── 存檔（固定輸出資料摘要） ──
    out_path = os.path.join(reports_dir, f"market_analysis_{date.today().strftime('%Y%m%d')}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"台股市場溫度分析報告  {date.today()}\n")
        f.write("=" * 65 + "\n\n")
        f.write("【指標摘要】\n")
        for s in stats:
            f.write(
                f"  {s['name']:<28}  當前 {s['current']:8.2f}"
                f"  百分位 {s['percentile']:5.1f}%  溫度分 {s['sentiment_pct']:5.1f}\n"
            )
        f.write(f"\n平均市場溫度：{avg:.1f} / 100\n")
        f.write("\n" + "=" * 65 + "\n\n")
        f.write("【分析 Prompt（可直接貼給 Claude）】\n\n")
        f.write(prompt + "\n\n")

        if use_api:
            # ── 呼叫 Claude API ──
            print("🤖 呼叫 Claude 分析中...\n")
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            msg = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            analysis = msg.content[0].text
            print(analysis)
            f.write("=" * 65 + "\n\n")
            f.write("【Claude 分析】\n\n")
            f.write(analysis + "\n")
        else:
            print("ℹ️  未設定 ANTHROPIC_API_KEY，跳過 API 呼叫。")
            print(f"   Prompt 已存入 {out_path}，可直接貼給 Claude 取得分析。\n")

    print(f"✅ 報告已儲存至 {out_path}")

    # ── 輸出 market_temp_latest.json（供 dashboard 使用）──
    import json
    json_data = {
        "generated_at": date.today().strftime("%Y-%m-%d"),
        "latest_date": latest_date,
        "taiex": taiex_now,
        "avg_sentiment": round(avg, 1),
        "stats": [
            {
                "name":          s["name"],
                "current":       round(s["current"], 2),
                "percentile":    round(s["percentile"], 1),
                "sentiment_pct": round(s["sentiment_pct"], 1),
                "min":    round(s["min"], 2),
                "p10":    round(s["p10"], 2),
                "p25":    round(s["p25"], 2),
                "median": round(s["median"], 2),
                "p75":    round(s["p75"], 2),
                "p90":    round(s["p90"], 2),
                "max":    round(s["max"], 2),
                "n":      s["n"],
            }
            for s in stats
        ],
    }
    json_path = "market_temp_latest.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"✅ 市場溫度資料已儲存至 {json_path}")


if __name__ == "__main__":
    main()
