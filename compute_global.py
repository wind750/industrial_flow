#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_global.py

讀 data/global_prices.csv，計算「全球資產」與「市場輪動」兩個 RRG 面板的座標，
輸出 web/rrg_data_global.js。

沿用 compute_rrg.py 的計算核心（compute_rs_ratio_momentum／clean_float／
PERIODS），不重複實作 RS-Ratio／RS-Momentum 公式或平滑層邏輯。

單一共用基準：全球股票 ACWI（stooq/yfinance 代碼 acwi.us）。

對齊規則（本檔負責實作）：
  對每個面板（資產／市場），取該面板實際用到的品項（基準 + 面板成員）在
  data/global_prices.csv 裡出現的日期範圍聯集，建出 Mon–Fri 工作日曆
  （pandas bdate_range），把每個品項的收盤價 reindex 到這個日曆上，再各自
  forward-fill 最多 5 個工作日；超過 5 個工作日仍缺值則維持 null（不臆測填
  補）。这样可以让欧美假日与台湾等亚洲市场假日不同步造成的缺口被合理地补
  上，但不会把长期无交易（下市、数据源缺段）的品项硬拉出一条假线。

輸出格式：
  window.RRG_DATASETS_GLOBAL = [
    {id:"assets", label:"全球資產", as_of, benchmarks:["全球股票 ACWI"],
     periods:[20,60,120], dates:[...], series:{...}, default_hidden:[]},
    {id:"markets", label:"市場輪動", ...}
  ];

用法：
  python compute_global.py
  python compute_global.py --out data\\_smoke_rrg_data_global.js
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# 重用 compute_rrg.py 的計算核心與週期常數；本檔只負責讀取全球資料、對齊
# 日曆、面板成員的迴圈組裝，實際 RS-Ratio／RS-Momentum 運算完全呼叫既有函式。
from compute_rrg import PERIODS, clean_float, compute_rs_ratio_momentum  # noqa: E402

DATA_DIR = SCRIPT_DIR / "data"
GLOBAL_CSV_PATH = DATA_DIR / "global_prices.csv"
DEFAULT_OUT_PATH = SCRIPT_DIR / "web" / "rrg_data_global.js"

MAX_DATES = 240
FFILL_LIMIT_BDAYS = 5

BENCHMARK_SYMBOL = "acwi.us"
BENCHMARK_LABEL = "全球股票 ACWI"

# (stooq代碼, 面板顯示名) —— 同一代碼在不同面板可以有不同顯示名
# （例如 spy.us 在資產面板顯示「美股 SPY」、在市場面板顯示「美國」）。
ASSET_PANEL: list[tuple[str, str]] = [
    ("spy.us", "美股 SPY"),
    ("qqq.us", "美股科技 QQQ"),
    ("tlt.us", "美債20年 TLT"),
    ("gld.us", "黃金 GLD"),
    ("uup.us", "美元 UUP"),
    ("vnq.us", "房地產 VNQ"),
    ("dbc.us", "商品 DBC"),
    ("btcusd", "比特幣 BTC"),
]

MARKET_PANEL: list[tuple[str, str]] = [
    ("spy.us", "美國"),
    ("ewt.us", "台灣"),
    ("ewj.us", "日本"),
    ("ewy.us", "南韓"),
    ("mchi.us", "中國"),
    ("inda.us", "印度"),
    ("vnm.us", "越南"),
    ("ewz.us", "巴西"),
    ("ewa.us", "澳洲"),
    ("ewu.us", "英國"),
    ("ewg.us", "德國"),
    ("vgk.us", "歐洲"),
]


def load_pivot() -> pd.DataFrame:
    """讀 CSV，轉成 date x symbol 的寬表格（收盤價），index 為 DatetimeIndex。"""
    df = pd.read_csv(GLOBAL_CSV_PATH, encoding="utf-8")
    pivot = df.pivot_table(index="date", columns="name", values="close", aggfunc="last")
    pivot.index = pd.to_datetime(pivot.index)
    pivot = pivot.sort_index()
    return pivot


def build_dataset(
    pivot: pd.DataFrame,
    dataset_id: str,
    dataset_label: str,
    panel: list[tuple[str, str]],
) -> dict:
    symbols_needed = [BENCHMARK_SYMBOL] + [s for s, _ in panel]
    symbols_present = [s for s in symbols_needed if s in pivot.columns]
    missing = sorted(set(symbols_needed) - set(symbols_present))
    if missing:
        print(f"警告: 資料集「{dataset_label}」缺少品項 {missing}，其序列將全為 null")

    if BENCHMARK_SYMBOL not in pivot.columns:
        print(f"錯誤: 找不到共用基準 {BENCHMARK_SYMBOL}，資料集「{dataset_label}」無法計算")
        return {
            "id": dataset_id,
            "label": dataset_label,
            "as_of": None,
            "benchmarks": [BENCHMARK_LABEL],
            "periods": PERIODS,
            "dates": [],
            "series": {BENCHMARK_LABEL: {}},
            "default_hidden": [],
        }

    sub = pivot[symbols_present].copy()
    valid_dates = sub.dropna(how="all").index
    if len(valid_dates) == 0:
        print(f"警告: 資料集「{dataset_label}」沒有任何有效資料")
        return {
            "id": dataset_id,
            "label": dataset_label,
            "as_of": None,
            "benchmarks": [BENCHMARK_LABEL],
            "periods": PERIODS,
            "dates": [],
            "series": {BENCHMARK_LABEL: {}},
            "default_hidden": [],
        }

    # ---- 對齊規則：品項聯集的工作日曆 + 各品項 forward-fill 上限 5 個工作日 ----
    calendar = pd.bdate_range(valid_dates.min(), valid_dates.max())
    aligned = sub.reindex(calendar)
    aligned = aligned.ffill(limit=FFILL_LIMIT_BDAYS)

    output_index = aligned.index[-MAX_DATES:]
    output_dates = [d.strftime("%Y-%m-%d") for d in output_index]
    n_out = len(output_dates)
    as_of = output_dates[-1] if output_dates else None

    benchmark_series = (
        aligned[BENCHMARK_SYMBOL]
        if BENCHMARK_SYMBOL in aligned.columns
        else pd.Series(index=aligned.index, dtype=float)
    )

    series: dict[str, dict[str, dict[str, list]]] = {BENCHMARK_LABEL: {}}
    for window in PERIODS:
        period_key = str(window)
        series[BENCHMARK_LABEL][period_key] = {}
        for symbol, label in panel:
            if symbol not in aligned.columns:
                series[BENCHMARK_LABEL][period_key][label] = [None] * n_out
                continue
            item_series = aligned[symbol]
            rs = 100 * item_series / benchmark_series
            rs_ratio, rs_momentum = compute_rs_ratio_momentum(rs, window)

            rs_ratio_out = rs_ratio.reindex(output_index)
            rs_momentum_out = rs_momentum.reindex(output_index)

            coords = []
            for i in range(n_out):
                x = clean_float(rs_ratio_out.iloc[i])
                y = clean_float(rs_momentum_out.iloc[i])
                coords.append(None if x is None or y is None else [x, y])
            series[BENCHMARK_LABEL][period_key][label] = coords

    return {
        "id": dataset_id,
        "label": dataset_label,
        "as_of": as_of,
        "benchmarks": [BENCHMARK_LABEL],
        "periods": PERIODS,
        "dates": output_dates,
        "series": series,
        "default_hidden": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="計算全球資產／市場輪動 RRG 座標並輸出 web/rrg_data_global.js")
    parser.add_argument("--out", type=str, default=None, help="輸出路徑（預設 web/rrg_data_global.js）")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else DEFAULT_OUT_PATH
    if not out_path.is_absolute():
        out_path = SCRIPT_DIR / out_path

    if not GLOBAL_CSV_PATH.exists():
        print(f"錯誤: 找不到 {GLOBAL_CSV_PATH}，請先執行 fetch_global.py")
        return 1

    pivot = load_pivot()
    if pivot.empty:
        print(f"錯誤: {GLOBAL_CSV_PATH} 沒有資料")
        return 1

    datasets = [
        build_dataset(pivot, "assets", "全球資產", ASSET_PANEL),
        build_dataset(pivot, "markets", "市場輪動", MARKET_PANEL),
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("window.RRG_DATASETS_GLOBAL = ")
        json.dump(datasets, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    print(f"已寫入 {out_path}")
    for ds in datasets:
        n_members = len(ds["series"].get(BENCHMARK_LABEL, {}).get(str(PERIODS[0]), {}))
        print(f"  {ds['id']} ({ds['label']}): as_of={ds['as_of']}, dates={len(ds['dates'])}, members={n_members}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
