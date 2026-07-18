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

DATA_CSV = SCRIPT_DIR / "data" / "sector_indices.csv"
RRG_HTML_PATH = SCRIPT_DIR / "web" / "rrg.html"


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
# Canvas 頁嵌入組裝
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def build_rrg_embed_html(as_of: str, rrg_html_mtime: float, _payload: dict) -> str:
    """把 web/rrg.html 原始碼中的 `<script src="rrg_data.js">` 換成內嵌
    `window.RRG_DATA = {...}`，組成可直接用 components.html 整頁嵌入的
    HTML。快取 key 用 as_of＋rrg.html 檔案 mtime（不含 payload 本身，
    payload 以底線前綴排除雜湊——資料量大，靠 as_of/mtime 已足以判斷是否
    需要重算），避免改版或換日後吃到舊快取。
    """
    raw_html = RRG_HTML_PATH.read_text(encoding="utf-8")
    payload_json = json.dumps(_payload, ensure_ascii=False)
    injected = raw_html.replace(
        '<script src="rrg_data.js"></script>',
        f"<script>window.RRG_DATA = {payload_json};</script>",
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


def main() -> None:
    st.set_page_config(page_title="產業輪動雷達 RRG", page_icon="🌅", layout="wide")
    inject_css()

    st.title("🌅 台股產業輪動雷達（RRG）")
    st.caption("Relative Rotation Graph — 觀察各產業相對大盤的資金輪動位置")

    pivot = load_pivot()

    if pivot.empty:
        st.error("找不到資料檔 data/sector_indices.csv，或檔案內容為空。")
        st.stop()

    with st.sidebar:
        st.header("使用說明")
        st.markdown(
            "**四象限判讀**\n\n"
            "- 🟡 **領先 Leading**（右上）：相對強度、動能皆優於大盤\n"
            "- 🟠 **弱化 Weakening**（右下）：仍強於大盤，但動能開始減弱\n"
            "- 🔵 **落後 Lagging**（左下）：相對強度、動能皆弱於大盤\n"
            "- 🔷 **改善 Improving**（左上）：仍弱於大盤，但動能正在回升\n\n"
            "族群通常依「改善 → 領先 → 弱化 → 落後」順時針方向在四象限間輪動。\n\n"
            "**剛轉強**：最近 5 個交易日內，由「改善」象限跨入「領先」象限的"
            "族群，圖表右側面板會即時標示（⚡ 剛轉強雷達）。\n\n"
            "基準指數、週期、回放範圍、尾巴長度與族群清單等控制項已整合在"
            "下方圖表內，請直接於圖表上操作。"
        )

    payload = build_rrg_payload(pivot)

    if not payload["dates"]:
        st.warning("目前資料量不足以計算 RRG 座標（歷史資料回補中），請稍後再試。")
        st.stop()

    rrg_html_mtime = RRG_HTML_PATH.stat().st_mtime if RRG_HTML_PATH.exists() else 0.0
    embed_html = build_rrg_embed_html(payload["as_of"], rrg_html_mtime, payload)
    # st.components.v1.html 已棄用（目前 streamlit 版本會在執行時警告，且已過官方
    # 移除期限），改用原生 st.iframe：同樣接受原始 HTML 字串直接嵌入，語意等價。
    st.iframe(embed_html, height=900)

    st.markdown(
        f'<div class="radar-footer">資料截至：{payload["as_of"]} ｜ 資料來源：臺灣證券交易所（TWSE）'
        f' ｜ RRG 座標為公開近似算法，非 JdK 原版公式</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
