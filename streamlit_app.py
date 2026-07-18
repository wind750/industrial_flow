#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
streamlit_app.py — 台股產業輪動雷達（RRG）雲端版

讀 data/sector_indices.csv，重用 compute_rrg.py 的 RRG 計算核心
（compute_rs_ratio_momentum），提供互動式四象限動畫視覺化。

部署：Streamlit Community Cloud，資料由 GitHub Actions 每日自動更新
（.github/workflows/update_data.yml）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# 重用 compute_rrg.py 的計算核心與常數（結構為單一輸出用途的 script，
# 僅其中純函式與常數適合直接 import；組裝 RRG_DATA 契約的迴圈在本檔自行
# 實作，但實際運算全部呼叫 compute_rrg.py 的函式，不重複貼上公式邏輯）。
from compute_rrg import (  # noqa: E402
    BENCHMARKS,
    DEFAULT_HIDDEN,
    MAX_DATES,
    PERIODS,
    clean_float,
    compute_rs_ratio_momentum,
)

# 全球資產／市場輪動兩面板同樣重用 compute_global.py 的面板定義與計算核心
# （build_dataset／build_multi_benchmark_dataset 內部呼叫的仍是 compute_rrg
# 的 compute_rs_ratio_momentum／clean_float，本檔不重複實作）。
# global_prices.csv 若不存在（例如本機尚未跑過 fetch_global.py），下面
# load_global_pivot() 回空表，兩個全球面板會被跳過，只嵌入台股面板，頁面
# 照常運作。
# 「全球資產」面板 v2 起為雙基準（ACWI＋DXY，見 ASSET_BENCHMARK_SPECS），
# 改呼叫 build_multi_benchmark_dataset；「市場輪動」面板維持單一基準 ACWI，
# 沿用原本的 build_dataset（其簽名對舊呼叫端維持相容，未變動這行呼叫）。
from compute_global import (  # noqa: E402
    ASSET_BENCHMARK_SPECS,
    ASSET_DEFAULT_HIDDEN,
    GLOBAL_CSV_PATH,
    MARKET_PANEL,
)
from compute_global import build_dataset as build_global_dataset  # noqa: E402
from compute_global import build_multi_benchmark_dataset as build_global_assets_dataset  # noqa: E402

# 上櫃類股面板重用 compute_tpex.py 暴露的 build_tpex_dataset（其內部同樣呼叫
# compute_rrg 的計算核心，不重複實作公式）。TPEX_CSV_PATH 不存在時
# load_tpex_pivot() 回空表，頁面據此略過上櫃面板，其餘面板照常運作。
from compute_tpex import TPEX_CSV_PATH  # noqa: E402
from compute_tpex import build_tpex_dataset  # noqa: E402

DATA_CSV = SCRIPT_DIR / "data" / "sector_indices.csv"
RRG_HTML_PATH = SCRIPT_DIR / "web" / "rrg.html"
STOCK_RANKINGS_JSON = SCRIPT_DIR / "data" / "stock_rankings.json"

# --------------------------------------------------------------------------
# 新手教學（內建於頁面，非另開文件）
# 與 web/rrg.html 內的教學浮層共用同一份 SVG 示意圖原始碼與文案，
# 兩處分開維護但視覺／文字內容須保持一致，改一處記得同步改另一處。
# --------------------------------------------------------------------------
RRG_TUTORIAL_SVG = """
<svg viewBox="0 0 560 430" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="RRG 四象限示意圖" style="width:100%;max-width:560px;height:auto;display:block;margin:0 auto;">
  <rect x="40" y="30" width="240" height="175" fill="rgba(21,94,138,0.13)"></rect>
  <rect x="280" y="30" width="240" height="175" fill="rgba(143,90,0,0.14)"></rect>
  <rect x="40" y="205" width="240" height="175" fill="rgba(74,98,136,0.13)"></rect>
  <rect x="280" y="205" width="240" height="175" fill="rgba(174,68,22,0.13)"></rect>
  <rect x="40" y="30" width="480" height="350" fill="none" stroke="rgba(120,90,40,0.4)" stroke-width="1.5"></rect>
  <line x1="280" y1="30" x2="280" y2="380" stroke="rgba(120,90,40,0.4)" stroke-width="1.5"></line>
  <line x1="40" y1="205" x2="520" y2="205" stroke="rgba(120,90,40,0.4)" stroke-width="1.5"></line>
  <circle cx="280" cy="205" r="120" fill="none" stroke="#8f5a00" stroke-width="2" stroke-opacity="0.55" stroke-dasharray="4 5"></circle>
  <polygon points="-7,-6 9,0 -7,6" fill="#8f5a00" transform="translate(280,85) rotate(0)"></polygon>
  <polygon points="-7,-6 9,0 -7,6" fill="#8f5a00" transform="translate(400,205) rotate(90)"></polygon>
  <polygon points="-7,-6 9,0 -7,6" fill="#8f5a00" transform="translate(280,325) rotate(180)"></polygon>
  <polygon points="-7,-6 9,0 -7,6" fill="#8f5a00" transform="translate(160,205) rotate(270)"></polygon>
  <circle cx="280" cy="205" r="3.5" fill="#2e2418"></circle>
  <text x="288" y="201" font-size="11" fill="#2e2418" font-weight="600">= 大盤</text>
  <text x="500" y="58" text-anchor="end" font-size="23" font-weight="700" fill="#8f5a00">領先</text>
  <text x="500" y="78" text-anchor="end" font-size="12" fill="#6b5a47">主流．資金聚集</text>
  <text x="60" y="58" text-anchor="start" font-size="23" font-weight="700" fill="#155e8a">改善</text>
  <text x="60" y="78" text-anchor="start" font-size="12" fill="#6b5a47">轉強中．潛力</text>
  <text x="500" y="338" text-anchor="end" font-size="23" font-weight="700" fill="#ae4416">弱化</text>
  <text x="500" y="358" text-anchor="end" font-size="12" fill="#6b5a47">退燒．留意</text>
  <text x="60" y="338" text-anchor="start" font-size="23" font-weight="700" fill="#4a6288">落後</text>
  <text x="60" y="358" text-anchor="start" font-size="12" fill="#6b5a47">冷宮．資金離開</text>
  <text x="280" y="405" text-anchor="middle" font-size="13" fill="#6b5a47">越往右 → 越強過大盤</text>
  <text x="18" y="205" text-anchor="middle" font-size="13" fill="#6b5a47" transform="rotate(-90 18 205)">越往上 → 動能越快</text>
</svg>
"""

RRG_TUTORIAL_HTML = (
    '<div class="glass-card" style="text-align:center;">' + RRG_TUTORIAL_SVG + "</div>"
    '<div class="glass-card">'
    '<h4 style="margin:0 0 10px;color:#4a3f2c;">如何看這張資金地圖</h4>'
    '<ol style="line-height:1.9;padding-left:20px;margin:0;font-size:14px;color:#4a3f2c;">'
    "<li><b>看圖只要記兩個方向</b>：點越往右 = 這個產業/資產<b>越強過大盤</b>；"
    "點越往上 = 它<b>變強的速度越快</b>。</li>"
    "<li><b>四象限</b>（配上方示意圖）："
    '<ul style="margin:6px 0;padding-left:18px;">'
    "<li>🟡 <b>領先</b>（右上・金）＝現在的主流</li>"
    "<li>🔷 <b>改善</b>（左上・藍）＝正在翻身值得盯</li>"
    "<li>🟠 <b>弱化</b>（右下・橘）＝開始退燒要小心</li>"
    "<li>🔵 <b>落後</b>（左下・藍灰）＝資金離開的冷宮</li>"
    "</ul></li>"
    "<li><b>最值錢的訊號</b>：從「改善」跨進「領先」＝原本沒人要的族群資金開始"
    "進來，畫面會自動標「⚡ 剛轉強」。</li>"
    "<li><b>怎麼操作</b>：按 ▶ 播放看資金這幾個月怎麼流動，尾巴是走過的路；"
    "覺得快可調慢速，想逐日看用「前一日／後一日」；上方可切四個面板——"
    "台股類股、上櫃類股、全球資產、市場輪動；週期短用 20 日、看大方向用 120 日；"
    "點產業名稱還能看該產業的「雙強個股」名單。</li>"
    "<li><b>建議看法</b>：先看「市場輪動」知道錢在哪國 → 再看「全球資產」知道"
    "市場愛冒險還是避險 → 最後回「台股類股」挑主流族群。</li>"
    '<li style="color:#7a6f5a;"><b>小提醒</b>：資料每天盤後自動更新，開網頁就是'
    "最新的；這是觀察資金流向的工具，不是買賣建議。</li>"
    "</ol></div>"
)


# --------------------------------------------------------------------------
# 資料載入與計算
# --------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="讀取產業指數資料中…")
def load_pivot() -> pd.DataFrame:
    """讀 CSV 並轉成 date x name 的寬表格，index 為 DatetimeIndex。"""
    if not DATA_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(DATA_CSV, encoding="utf-8")
    if df.empty:
        return pd.DataFrame()
    pivot = df.pivot_table(index="date", columns="name", values="close", aggfunc="last")
    pivot = pivot.sort_index()
    pivot.index = pd.to_datetime(pivot.index)
    return pivot


@st.cache_data(ttl=3600, show_spinner=False)
def build_rrg_payload(pivot: pd.DataFrame) -> dict:
    """比照 compute_rrg.py main() 的資料契約，組出 web/rrg.html 的
    window.RRG_DATA 結構（as_of / benchmarks / periods / dates / series /
    default_hidden）。實際的 RS-Ratio／RS-Momentum 運算全部重用
    compute_rs_ratio_momentum；本函式只負責迴圈組裝與 JSON 友善的清理，
    不重複實作公式。
    """
    all_dates = list(pivot.index)
    if not all_dates:
        return {
            "as_of": None,
            "benchmarks": BENCHMARKS,
            "periods": PERIODS,
            "dates": [],
            "series": {},
            "default_hidden": DEFAULT_HIDDEN,
        }

    sector_names = sorted(c for c in pivot.columns if c.endswith("類指數"))
    output_dates = all_dates[-MAX_DATES:]
    n_out = len(output_dates)
    date_labels = [d.strftime("%Y-%m-%d") for d in output_dates]
    as_of = date_labels[-1]

    series: dict[str, dict[str, dict[str, list]]] = {}
    for benchmark in BENCHMARKS:
        series[benchmark] = {}
        benchmark_series = (
            pivot[benchmark] if benchmark in pivot.columns else pd.Series(index=pivot.index, dtype=float)
        )
        for window in PERIODS:
            period_key = str(window)
            series[benchmark][period_key] = {}
            for sector in sector_names:
                rs = 100 * pivot[sector] / benchmark_series
                rs_ratio, rs_momentum = compute_rs_ratio_momentum(rs, window)
                rs_ratio_out = rs_ratio.reindex(output_dates)
                rs_momentum_out = rs_momentum.reindex(output_dates)

                coords = []
                for i in range(n_out):
                    x = clean_float(rs_ratio_out.iloc[i])
                    y = clean_float(rs_momentum_out.iloc[i])
                    coords.append(None if x is None or y is None else [x, y])
                series[benchmark][period_key][sector] = coords

    return {
        "as_of": as_of,
        "benchmarks": BENCHMARKS,
        "periods": PERIODS,
        "dates": date_labels,
        "series": series,
        "default_hidden": DEFAULT_HIDDEN,
    }


# --------------------------------------------------------------------------
# 全球資產／市場輪動面板資料載入與計算
# --------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="讀取全球資產／市場資料中…")
def load_global_pivot() -> pd.DataFrame:
    """讀 data/global_prices.csv 並轉成 date x symbol 的寬表格。檔案不存在或
    為空時回傳空 DataFrame——呼叫端據此判斷是否要嵌入全球面板，不是錯誤。
    """
    if not GLOBAL_CSV_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(GLOBAL_CSV_PATH, encoding="utf-8")
    if df.empty:
        return pd.DataFrame()
    pivot = df.pivot_table(index="date", columns="name", values="close", aggfunc="last")
    pivot.index = pd.to_datetime(pivot.index)
    pivot = pivot.sort_index()
    return pivot


@st.cache_data(ttl=3600, show_spinner=False)
def build_global_datasets(pivot_global: pd.DataFrame) -> list[dict]:
    """組出 window.RRG_DATASETS_GLOBAL 陣列（"全球資產"／"市場輪動"兩個
    dataset）。實際運算全部呼叫 compute_global.build_multi_benchmark_dataset／
    build_dataset（其內部再呼叫 compute_rrg 的核心函式），本函式只負責依
    pivot 是否有資料決定要不要組。「全球資產」面板為雙基準（ACWI＋DXY），
    「市場輪動」面板維持單一基準 ACWI。
    """
    if pivot_global.empty:
        return []
    return [
        build_global_assets_dataset(pivot_global, "assets", "全球資產", ASSET_BENCHMARK_SPECS, ASSET_DEFAULT_HIDDEN),
        build_global_dataset(pivot_global, "markets", "市場輪動", MARKET_PANEL),
    ]


# --------------------------------------------------------------------------
# 上櫃類股面板資料載入與計算
# --------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="讀取上櫃產業指數資料中…")
def load_tpex_pivot() -> pd.DataFrame:
    """讀 data/tpex_indices.csv 並轉成 date x name 的寬表格，index 為
    DatetimeIndex。檔案不存在或為空時回傳空 DataFrame——呼叫端據此判斷是否
    要嵌入上櫃面板，不是錯誤（例如本機尚未跑過 fetch_tpex_sector.py）。
    """
    if not TPEX_CSV_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(TPEX_CSV_PATH, encoding="utf-8")
    if df.empty:
        return pd.DataFrame()
    pivot = df.pivot_table(index="date", columns="name", values="close", aggfunc="last")
    pivot.index = pd.to_datetime(pivot.index)
    pivot = pivot.sort_index()
    return pivot


@st.cache_data(ttl=3600, show_spinner=False)
def build_tpex_payload(pivot_tpex: pd.DataFrame) -> dict | None:
    """組出 window.RRG_DATASET_TPEX（"上櫃類股"面板，單一基準「櫃買指數」）。
    實際運算全部呼叫 compute_tpex.build_tpex_dataset（其內部再呼叫
    compute_rrg 的核心函式），本函式只負責依 pivot 是否有資料決定要不要組。
    pivot 為空（CSV 不存在／無資料）時回傳 None，呼叫端據此略過上櫃面板。
    """
    if pivot_tpex.empty:
        return None
    dataset = build_tpex_dataset(pivot_tpex)
    if not dataset["dates"]:
        return None
    return dataset


# --------------------------------------------------------------------------
# 個股雙強排行載入（前端消費端；運算已在 compute_stocks.py 離線完成，本檔
# 只負責讀取與呈現，不重算）
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_stock_rankings(mtime: float) -> dict | None:
    """讀 data/stock_rankings.json（compute_stocks.py 的產物，純讀取不重算）。
    檔案不存在時回傳 None，呼叫端據此整區隱藏（expander 不顯示、iframe 內也
    不注入 window.RRG_STOCK_RANKINGS），不是錯誤——與其餘選用面板（上櫃／
    全球資產）的既有容錯慣例一致。快取 key 用檔案 mtime（`mtime` 參數，
    呼叫端傳入；刻意不加底線前綴——底線前綴在 st.cache_data 語意是「排除在
    雜湊 key 之外」，這裡恰好要用 mtime 當雜湊 key 才能在檔案更新後正確
    失效重讀，若加底線前綴會導致快取永遠不失效，吃到舊資料或誤判缺檔），
    檔案更新後會自動重新讀取，不吃到舊快取。
    """
    if not STOCK_RANKINGS_JSON.exists():
        return None
    try:
        with STOCK_RANKINGS_JSON.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or not payload.get("panels"):
        return None
    return payload


# --------------------------------------------------------------------------
# Canvas 頁嵌入組裝
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def build_rrg_embed_html(
    as_of: str,
    global_as_of: str | None,
    tpex_as_of: str | None,
    stock_as_of: str | None,
    rrg_html_mtime: float,
    _payload: dict,
    _global_datasets: list[dict],
    _tpex_payload: dict | None,
    _stock_payload: dict | None,
) -> str:
    """把 web/rrg.html 原始碼中的四個 `<script src="...">` 都換成內嵌資料：
    `window.RRG_DATA = {...}`（台股）、（若有上櫃資料）
    `window.RRG_DATASET_TPEX = {...}`（上櫃類股）、（若有全球資料）
    `window.RRG_DATASETS_GLOBAL = [...]`（全球資產／市場輪動）與（若有個股
    雙強排行資料）`window.RRG_STOCK_RANKINGS = {...}`，組成可直接用
    st.iframe 整頁嵌入的 HTML。任一資料缺席時保留原本的
    `<script src="..." onerror="...">` 標籤原樣——在 iframe srcdoc 環境下
    這個相對路徑本來就抓不到檔案，onerror 靜默處理，rrg.html 的 JS 本就會
    檢查對應的 window 全域變數是否存在，頁面仍只顯示可用的功能，不會出錯。

    快取 key 用 as_of／global_as_of／tpex_as_of／stock_as_of／rrg.html 檔案
    mtime（不含 payload 本身，payload 以底線前綴排除雜湊——資料量大，靠
    as_of/mtime 已足以判斷是否需要重算），避免改版或換日後吃到舊快取。
    """
    raw_html = RRG_HTML_PATH.read_text(encoding="utf-8")
    payload_json = json.dumps(_payload, ensure_ascii=False)
    injected = raw_html.replace(
        '<script src="rrg_data.js"></script>',
        f"<script>window.RRG_DATA = {payload_json};</script>",
        1,
    )
    if _tpex_payload:
        tpex_json = json.dumps(_tpex_payload, ensure_ascii=False)
        injected = injected.replace(
            '<script src="rrg_data_tpex.js" onerror="window.__RRG_TPEX_MISSING__ = true;"></script>',
            f"<script>window.RRG_DATASET_TPEX = {tpex_json};</script>",
            1,
        )
    if _global_datasets:
        global_json = json.dumps(_global_datasets, ensure_ascii=False)
        injected = injected.replace(
            '<script src="rrg_data_global.js" onerror="window.__RRG_GLOBAL_MISSING__ = true;"></script>',
            f"<script>window.RRG_DATASETS_GLOBAL = {global_json};</script>",
            1,
        )
    if _stock_payload:
        stock_json = json.dumps(_stock_payload, ensure_ascii=False)
        injected = injected.replace(
            '<script src="rrg_data_stocks.js" onerror="window.__RRG_STOCKS_MISSING__ = true;"></script>',
            f"<script>window.RRG_STOCK_RANKINGS = {stock_json};</script>",
            1,
        )
    # iframe 內不需要再出現自己的捲軸：整頁已經是單一視圖，避免雙捲軸
    injected = injected.replace(
        "html, body {\n    margin: 0; padding: 0;",
        "html, body {\n    margin: 0; padding: 0; overflow: hidden;",
        1,
    )
    return injected


# --------------------------------------------------------------------------
# Streamlit 頁面
# --------------------------------------------------------------------------
def inject_css() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background: linear-gradient(135deg, #FFF8EC 0%, #FDF3E3 25%, #F5F0E6 55%, #EAF3E6 100%);
            background-attachment: fixed;
        }
        .stApp::before {
            content: "";
            position: fixed;
            inset: 0;
            background: linear-gradient(120deg, rgba(212,160,23,0.10) 0%, rgba(212,160,23,0.0) 30%),
                        linear-gradient(300deg, rgba(111,169,106,0.08) 0%, rgba(111,169,106,0.0) 35%);
            pointer-events: none;
            z-index: 0;
        }
        section[data-testid="stSidebar"] {
            background: rgba(255, 250, 240, 0.55);
            backdrop-filter: blur(10px);
            border-right: 1px solid rgba(212,160,23,0.25);
        }
        div[data-testid="stVerticalBlockBorderWrapper"], div.stMetric, .glass-card {
            background: rgba(255, 255, 255, 0.42);
            backdrop-filter: blur(8px);
            border-radius: 16px;
            border: 1px solid rgba(212,160,23,0.35);
        }
        .glass-card {
            padding: 14px 18px;
            margin-bottom: 12px;
            box-shadow: 0 4px 18px rgba(120,90,20,0.08);
        }
        h1, h2, h3 { color: #4a3f2c !important; }
        .radar-footer {
            margin-top: 24px;
            padding-top: 10px;
            border-top: 1px solid rgba(212,160,23,0.3);
            color: #7a6f5a;
            font-size: 0.85rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_stock_rankings_expander(stock_rankings: dict | None) -> None:
    """iframe 下方的「雙強個股排行」摺疊區：市場／產業／週期三個篩選器 +
    st.dataframe 呈現 TOP 10。純讀取 data/stock_rankings.json 已算好的結果
    （見 load_stock_rankings），不重算任何 RS-Ratio／RS-Momentum。
    `stock_rankings` 為 None（檔案不存在／內容無效）時整區不顯示，不報錯，
    比照上櫃／全球面板既有的「缺檔靜默略過」慣例。
    """
    if not stock_rankings:
        return
    panels = stock_rankings.get("panels") or {}
    market_options = [(key, label) for key, label in (("twse", "上市"), ("tpex", "上櫃")) if panels.get(key)]
    if not market_options:
        return

    with st.expander("⚡ 雙強個股排行（強產業內選強個股）", expanded=False):
        st.caption(
            "在已轉強的產業內，找出相對「自身產業指數」更強的個股（RS-Ratio ≥ 100）。"
            "只是把既有 RRG 的「產業 vs. 大盤」換成「個股 vs. 所屬產業指數」，往下鑽一層。"
        )
        col_market, col_industry, col_period = st.columns([1, 2, 2])
        with col_market:
            market_key = st.selectbox(
                "市場", options=[k for k, _ in market_options],
                format_func=lambda k: dict(market_options)[k], key="stock_rank_market",
            )
        industries = sorted(panels.get(market_key) or {})
        with col_industry:
            industry = st.selectbox(
                "產業", options=industries,
                format_func=lambda name: name[:-3] if name.endswith("類指數") else name,
                key="stock_rank_industry_" + market_key,
            )
        with col_period:
            period = st.radio(
                "週期", options=[20, 60, 120], index=1, horizontal=True,
                format_func=lambda p: f"{p}日", key="stock_rank_period",
            )

        rows = ((panels.get(market_key) or {}).get(industry) or {}).get(str(period)) or []
        if not rows:
            st.info("本產業目前無符合雙強條件的個股。")
        else:
            df = pd.DataFrame(sorted(rows, key=lambda r: -r["x"]))
            df = df.rename(columns={
                "code": "代號", "name": "名稱", "x": "RS-Ratio", "y": "RS-Momentum", "amt20": "20日均額(億元)",
            })
            df["20日均額(億元)"] = (df["20日均額(億元)"] / 1e8).round(1)
            df["RS-Ratio"] = df["RS-Ratio"].round(2)
            df["RS-Momentum"] = df["RS-Momentum"].round(2)
            df = df[["代號", "名稱", "RS-Ratio", "RS-Momentum", "20日均額(億元)"]]
            st.dataframe(df, width="stretch", hide_index=True)

        liquidity_floor_wan = (stock_rankings.get("liquidity_floor") or 0) / 1e4
        st.caption(
            f"流動性門檻：20日均成交金額 ≥ {liquidity_floor_wan:,.0f} 萬｜資料截至 "
            f"{stock_rankings.get('as_of', '--')}｜觀察工具，未經回測驗證，非買賣建議"
        )


def main() -> None:
    st.set_page_config(page_title="產業輪動雷達 RRG", page_icon="🌅", layout="wide")
    inject_css()

    st.title("🌅 台股產業輪動雷達（RRG）")
    st.caption("Relative Rotation Graph — 觀察各產業相對大盤的資金輪動位置")

    with st.expander("📖 新手教學 — 第一次來先看這裡", expanded=False):
        st.markdown(RRG_TUTORIAL_HTML, unsafe_allow_html=True)

    pivot = load_pivot()

    if pivot.empty:
        st.error("找不到資料檔 data/sector_indices.csv，或檔案內容為空。")
        st.stop()

    with st.sidebar:
        st.header("使用說明")
        st.markdown(
            "圖文版四象限教學已整合到主畫面上方的「📖 新手教學」摺疊區，"
            "第一次使用建議先展開看一輪。這裡只列操作細節：\n\n"
            "**剛轉強**：最近 5 個交易日內，由「改善」象限跨入「領先」象限的"
            "族群，圖表右側面板會即時標示（⚡ 剛轉強雷達）。\n\n"
            "基準指數、週期、回放範圍、尾巴長度與族群清單等控制項已整合在"
            "下方圖表內，請直接於圖表上操作。\n\n"
            "**面板**：圖表左上角可切換「台股類股／上櫃類股／全球資產／"
            "市場輪動」四個面板；「上櫃類股」以「櫃買指數」為單一基準；"
            "「市場輪動」以「全球股票 ACWI」為基準，「全球資產」可切換"
            "「全球股票 ACWI」與「美元指數 DXY」雙基準（切到 DXY 時清單會排除"
            "「美元 UUP」自身）。"
        )

    payload = build_rrg_payload(pivot)

    if not payload["dates"]:
        st.warning("目前資料量不足以計算 RRG 座標（歷史資料回補中），請稍後再試。")
        st.stop()

    pivot_global = load_global_pivot()
    global_datasets = build_global_datasets(pivot_global)
    global_as_of = global_datasets[0]["as_of"] if global_datasets else None

    pivot_tpex = load_tpex_pivot()
    tpex_payload = build_tpex_payload(pivot_tpex)
    tpex_as_of = tpex_payload["as_of"] if tpex_payload else None

    stock_mtime = STOCK_RANKINGS_JSON.stat().st_mtime if STOCK_RANKINGS_JSON.exists() else 0.0
    stock_rankings = load_stock_rankings(stock_mtime)
    stock_as_of = stock_rankings["as_of"] if stock_rankings else None

    rrg_html_mtime = RRG_HTML_PATH.stat().st_mtime if RRG_HTML_PATH.exists() else 0.0
    embed_html = build_rrg_embed_html(
        payload["as_of"], global_as_of, tpex_as_of, stock_as_of, rrg_html_mtime,
        payload, global_datasets, tpex_payload, stock_rankings,
    )
    # st.components.v1.html 已棄用（目前 streamlit 版本會在執行時警告，且已過官方
    # 移除期限），改用原生 st.iframe：同樣接受原始 HTML 字串直接嵌入，語意等價。
    # 高度從 900 降到 780：rrg.html 內部用 100vh 撐滿自己的 iframe 視窗，這個
    # 數字同時決定 CSS 的 max-width:720px + orientation:portrait 斷點是否成立
    # （手機版 iframe 寬度通常 <720px，780 高 > 寬 -> portrait 成立 -> 觸發窄
    # 直版佈局；桌面版 iframe 寬度遠大於 720，斷點不成立，維持原本左右分欄）。
    # 780 在窄直版堆疊佈局下已足夠放下 header＋圖表＋播放列＋抽屜把手，也比
    # 900 更貼近手機瀏覽器實際可視高度，減少手機上需要額外下拉的空白。
    st.iframe(embed_html, height=780)

    render_stock_rankings_expander(stock_rankings)

    footer_bits = [f"台股資料截至：{payload['as_of']}"]
    if tpex_as_of:
        footer_bits.append(f"上櫃資料截至：{tpex_as_of}")
    else:
        footer_bits.append("上櫃資料尚未產出（未執行 fetch_tpex_sector.py，僅顯示其餘面板）")
    if global_as_of:
        footer_bits.append(f"全球資料截至：{global_as_of}")
    else:
        footer_bits.append("全球資料尚未產出（未執行 fetch_global.py，僅顯示台股面板）")
    footer_text = " ｜ ".join(footer_bits)
    st.markdown(
        f'<div class="radar-footer">{footer_text} ｜ 資料來源：臺灣證券交易所（TWSE）、'
        f'stooq／Yahoo Finance（yfinance，非官方免費源） ｜ RRG 座標為公開近似算法，非 JdK 原版公式</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
