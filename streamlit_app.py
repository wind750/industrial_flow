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

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# 重用 compute_rrg.py 的計算核心與常數（結構為單一輸出用途的 script，
# 僅其中純函式與常數適合直接 import；互動式切片邏輯在本檔自行實作）。
from compute_rrg import (  # noqa: E402
    BENCHMARKS,
    DEFAULT_HIDDEN,
    PERIODS,
    compute_rs_ratio_momentum,
)

DATA_CSV = SCRIPT_DIR / "data" / "sector_indices.csv"

PERIOD_LABELS = {20: "20 日／短線", 60: "60 日／波段", 120: "120 日／長期"}
RANGE_MONTHS = {"1 個月": 1, "3 個月": 3, "6 個月": 6, "12 個月": 12}

# ---- 太陽龐克配色 ----
COLOR_LEADING = "#D4A017"      # 領先：太陽金（暖＝比大盤強）
COLOR_WEAKENING = "#E8703A"    # 弱化：夕陽橘（暖，動能退潮）
COLOR_LAGGING = "#7C90AD"      # 落後：霧藍（冷＝比大盤弱）
COLOR_IMPROVING = "#2E9BD6"    # 改善：科技藍（冷，動能翻揚）
COLOR_TAIL_LINE = "rgba(120,110,90,0.35)"
COLOR_CROSSHAIR = "rgba(120,110,90,0.55)"

QUADRANT_META = {
    "leading": ("領先", COLOR_LEADING),
    "weakening": ("弱化", COLOR_WEAKENING),
    "lagging": ("落後", COLOR_LAGGING),
    "improving": ("改善", COLOR_IMPROVING),
}


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
def compute_all_coords(pivot: pd.DataFrame, benchmark: str, window: int) -> pd.DataFrame:
    """對所有「類指數」族群計算 rs_ratio / rs_momentum 全歷史序列。

    回傳 long-form DataFrame: date, name, x (rs_ratio), y (rs_momentum)。
    """
    sector_names = sorted(c for c in pivot.columns if c.endswith("類指數"))
    if benchmark not in pivot.columns or not sector_names:
        return pd.DataFrame(columns=["date", "name", "x", "y"])

    benchmark_series = pivot[benchmark]
    frames = []
    for sector in sector_names:
        rs = 100 * pivot[sector] / benchmark_series
        rs_ratio, rs_momentum = compute_rs_ratio_momentum(rs, window)
        frames.append(
            pd.DataFrame(
                {
                    "date": pivot.index,
                    "name": sector,
                    "x": rs_ratio.values,
                    "y": rs_momentum.values,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def classify_quadrant(x: float, y: float) -> str | None:
    if pd.isna(x) or pd.isna(y):
        return None
    if x >= 100 and y >= 100:
        return "leading"
    if x >= 100 and y < 100:
        return "weakening"
    if x < 100 and y < 100:
        return "lagging"
    return "improving"


def find_recently_turned_strong(coords: pd.DataFrame, sectors: list[str]) -> list[str]:
    """找出最近 5 個交易日內，從「改善」跨入「領先」象限的族群。"""
    if coords.empty:
        return []
    all_dates = sorted(coords["date"].unique())
    recent_dates = all_dates[-6:]  # 需要相鄰 pair，故取 6 個交易日算 5 段轉換
    if len(recent_dates) < 2:
        return []

    result = []
    for sector in sectors:
        sub = coords[coords["name"] == sector].set_index("date")
        cats = []
        for d in recent_dates:
            if d in sub.index:
                row = sub.loc[d]
                cats.append(classify_quadrant(row["x"], row["y"]))
            else:
                cats.append(None)
        for i in range(1, len(cats)):
            if cats[i - 1] == "improving" and cats[i] == "leading":
                result.append(sector)
                break
    return result


# --------------------------------------------------------------------------
# 圖表建構
# --------------------------------------------------------------------------
def build_figure(
    coords: pd.DataFrame,
    sectors: list[str],
    output_dates: list[pd.Timestamp],
    tail_len: int,
    show_labels: bool = True,
) -> go.Figure:
    fig = go.Figure()

    if not sectors or not output_dates:
        fig.update_layout(
            annotations=[
                dict(
                    text="資料不足，尚無法繪製 RRG（歷史資料回補中）",
                    xref="paper",
                    yref="paper",
                    x=0.5,
                    y=0.5,
                    showarrow=False,
                    font=dict(size=16, color="#6b5f4a"),
                )
            ],
            height=600,
        )
        return fig

    # 依 (name, date) 建索引方便查找
    lookup = coords.set_index(["name", "date"])[["x", "y"]]

    def get_xy(sector: str, d: pd.Timestamp) -> tuple[float | None, float | None]:
        try:
            row = lookup.loc[(sector, d)]
            x, y = row["x"], row["y"]
            if pd.isna(x) or pd.isna(y):
                return None, None
            return float(x), float(y)
        except KeyError:
            return None, None

    # 計算座標軸範圍（置中於 100,100，對稱留白）
    valid_x = coords.loc[coords["name"].isin(sectors), "x"].dropna()
    valid_y = coords.loc[coords["name"].isin(sectors), "y"].dropna()
    if len(valid_x) and len(valid_y):
        pad = max(4.0, float(max((valid_x - 100).abs().max(), (valid_y - 100).abs().max())) * 1.15)
    else:
        pad = 5.0
    axis_min, axis_max = 100 - pad, 100 + pad

    def frame_traces(idx: int) -> list[go.Scatter]:
        d = output_dates[idx]
        start = max(0, idx - tail_len + 1)
        window_dates = output_dates[start : idx + 1]
        traces = []
        for sector in sectors:
            xs, ys, colors, sizes, hover_texts = [], [], [], [], []
            for wd in window_dates:
                x, y = get_xy(sector, wd)
                xs.append(x)
                ys.append(y)
                cat = classify_quadrant(x, y) if x is not None else None
                colors.append(QUADRANT_META[cat][1] if cat else "rgba(0,0,0,0)")
                hover_texts.append(f"{sector}<br>{wd.strftime('%Y-%m-%d')}<br>RS-Ratio: {x:.2f}<br>RS-Momentum: {y:.2f}" if x is not None else "")
            sizes = [7] * len(xs)
            if sizes:
                sizes[-1] = 15
            # 常駐名稱標籤只掛在最新一點（尾巴其餘點留空），避免整條軌跡都是字
            point_labels = [""] * len(xs)
            if point_labels and xs and xs[-1] is not None:
                point_labels[-1] = sector.replace("類指數", "")
            traces.append(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="lines+markers+text" if show_labels else "lines+markers",
                    line=dict(color=COLOR_TAIL_LINE, width=1.5),
                    marker=dict(color=colors, size=sizes, line=dict(color="rgba(255,255,255,0.6)", width=1)),
                    name=sector,
                    text=point_labels,
                    textposition="top center",
                    textfont=dict(size=10, color="#5b4a37"),
                    hovertext=hover_texts,
                    hoverinfo="text",
                    showlegend=False,
                )
            )
        return traces

    # 初始畫面顯示最新一個交易日
    last_idx = len(output_dates) - 1
    fig.add_traces(frame_traces(last_idx))

    frames = [
        go.Frame(name=d.strftime("%Y-%m-%d"), data=frame_traces(i))
        for i, d in enumerate(output_dates)
    ]
    fig.frames = frames

    # 十字中心線
    fig.add_shape(type="line", x0=100, x1=100, y0=axis_min, y1=axis_max, line=dict(color=COLOR_CROSSHAIR, width=1, dash="dot"))
    fig.add_shape(type="line", x0=axis_min, x1=axis_max, y0=100, y1=100, line=dict(color=COLOR_CROSSHAIR, width=1, dash="dot"))

    # 象限底色
    fig.add_shape(type="rect", x0=100, x1=axis_max, y0=100, y1=axis_max, fillcolor=COLOR_LEADING, opacity=0.08, line_width=0, layer="below")
    fig.add_shape(type="rect", x0=100, x1=axis_max, y0=axis_min, y1=100, fillcolor=COLOR_WEAKENING, opacity=0.08, line_width=0, layer="below")
    fig.add_shape(type="rect", x0=axis_min, x1=100, y0=axis_min, y1=100, fillcolor=COLOR_LAGGING, opacity=0.08, line_width=0, layer="below")
    fig.add_shape(type="rect", x0=axis_min, x1=100, y0=100, y1=axis_max, fillcolor=COLOR_IMPROVING, opacity=0.08, line_width=0, layer="below")

    label_pad = pad * 0.92
    fig.add_annotation(x=100 + label_pad, y=100 + label_pad, text="領先", showarrow=False, font=dict(size=14, color=COLOR_LEADING), xanchor="right", yanchor="top")
    fig.add_annotation(x=100 + label_pad, y=100 - label_pad, text="弱化", showarrow=False, font=dict(size=14, color=COLOR_WEAKENING), xanchor="right", yanchor="bottom")
    fig.add_annotation(x=100 - label_pad, y=100 - label_pad, text="落後", showarrow=False, font=dict(size=14, color=COLOR_LAGGING), xanchor="left", yanchor="bottom")
    fig.add_annotation(x=100 - label_pad, y=100 + label_pad, text="改善", showarrow=False, font=dict(size=14, color=COLOR_IMPROVING), xanchor="left", yanchor="top")

    slider_steps = [
        dict(
            method="animate",
            args=[[f.name], dict(mode="immediate", frame=dict(duration=120, redraw=False), transition=dict(duration=100, easing="cubic-in-out"))],
            label=f.name,
        )
        for f in frames
    ]

    fig.update_layout(
        height=640,
        xaxis=dict(title="RS-Ratio", range=[axis_min, axis_max], gridcolor="rgba(120,110,90,0.12)", zeroline=False),
        yaxis=dict(title="RS-Momentum", range=[axis_min, axis_max], gridcolor="rgba(120,110,90,0.12)", zeroline=False),
        plot_bgcolor="rgba(255,255,255,0.35)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#4a3f2c"),
        margin=dict(l=20, r=20, t=30, b=20),
        sliders=[
            dict(
                active=last_idx,
                steps=slider_steps,
                x=0.05,
                len=0.9,
                currentvalue=dict(prefix="日期："),
            )
        ],
        updatemenus=[
            dict(
                type="buttons",
                direction="left",
                x=0.05,
                y=1.12,
                showactive=False,
                buttons=[
                    dict(
                        label="🐢 慢速",
                        method="animate",
                        args=[None, dict(frame=dict(duration=900, redraw=False), fromcurrent=True, transition=dict(duration=820, easing="cubic-in-out"))],
                    ),
                    dict(
                        label="▶ 播放",
                        method="animate",
                        args=[None, dict(frame=dict(duration=480, redraw=False), fromcurrent=True, transition=dict(duration=430, easing="cubic-in-out"))],
                    ),
                    dict(
                        label="⏩ 快播",
                        method="animate",
                        args=[None, dict(frame=dict(duration=160, redraw=False), fromcurrent=True, transition=dict(duration=140, easing="linear"))],
                    ),
                    dict(
                        label="⏸ 暫停",
                        method="animate",
                        args=[[None], dict(mode="immediate", frame=dict(duration=0, redraw=False), transition=dict(duration=0))],
                    ),
                ],
            )
        ],
    )
    return fig


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

    available_sectors = sorted(c for c in pivot.columns if c.endswith("類指數"))
    available_benchmarks = [b for b in BENCHMARKS if b in pivot.columns]
    if not available_benchmarks:
        available_benchmarks = BENCHMARKS  # 讓使用者仍可選，計算時會顯示 NaN

    with st.sidebar:
        st.header("控制面板")

        benchmark = st.selectbox("基準指數", available_benchmarks, index=0)

        period = st.select_slider(
            "週期",
            options=PERIODS,
            value=60 if 60 in PERIODS else PERIODS[-1],
            format_func=lambda w: PERIOD_LABELS.get(w, str(w)),
        )

        range_label = st.selectbox("回放範圍", list(RANGE_MONTHS.keys()), index=3)

        tail_len = st.slider("尾巴長度（交易日）", min_value=5, max_value=60, value=10, step=1)

        show_labels = st.checkbox("顯示族群名稱", value=True, help="在每個點旁標註族群短名；點太擠時可暫時關閉")

        default_sectors = [s for s in available_sectors if s not in DEFAULT_HIDDEN]
        sectors = st.multiselect("族群", options=available_sectors, default=default_sectors)

    if not sectors:
        st.warning("請至少選擇一個族群。")
        st.stop()

    # 全歷史計算（rolling 需要完整序列），之後再依回放範圍裁切輸出日期
    coords_full = compute_all_coords(pivot, benchmark, period)

    if coords_full.empty:
        st.warning("目前資料量不足以計算 RRG 座標（歷史資料回補中），請稍後再試。")
        st.stop()

    months = RANGE_MONTHS[range_label]
    latest_date = pivot.index.max()
    cutoff = latest_date - pd.DateOffset(months=months)
    output_dates = [d for d in pivot.index if d >= cutoff]

    # 剛轉強清單：以完整歷史（不受回放範圍限制）判斷最近 5 個交易日的轉強
    turned_strong = find_recently_turned_strong(coords_full, sectors)

    if turned_strong:
        st.markdown(
            f'<div class="glass-card">🚀 <b>剛轉強</b>（最近 5 個交易日由「改善」跨入「領先」象限）：'
            f'{"、".join(turned_strong)}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="glass-card">目前沒有族群在最近 5 個交易日內剛跨入領先象限。</div>',
            unsafe_allow_html=True,
        )

    fig = build_figure(coords_full, sectors, output_dates, tail_len, show_labels)
    st.plotly_chart(fig, use_container_width=True)

    as_of = latest_date.strftime("%Y-%m-%d")
    st.markdown(
        f'<div class="radar-footer">資料截至：{as_of} ｜ 資料來源：臺灣證券交易所（TWSE）'
        f' ｜ RRG 座標為公開近似算法，非 JdK 原版公式</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
