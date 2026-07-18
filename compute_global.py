#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_global.py

讀 data/global_prices.csv，計算「全球資產」與「市場輪動」兩個 RRG 面板的座標，
輸出 web/rrg_data_global.js。

沿用 compute_rrg.py 的計算核心（compute_rs_ratio_momentum／clean_float／
PERIODS），不重複實作 RS-Ratio／RS-Momentum 公式或平滑層邏輯。

「市場輪動」面板單一共用基準：全球股票 ACWI（stooq/yfinance 代碼 acwi.us）。
「全球資產」面板（v2）為雙基準：全球股票 ACWI ＋ 美元指數 DXY（yfinance 代碼
DX-Y.NYB，stooq 無此代碼）。兩個基準的面板成員集合不完全相同——DXY 基準下
排除「美元 UUP」（自己除自己無意義），其餘成員兩基準共用同一份清單。

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

# 「市場輪動」面板沿用單一基準 ACWI；BENCHMARK_SYMBOL/BENCHMARK_LABEL 這兩個
# 名字保留給 build_dataset() 的預設參數值，維持舊呼叫端（streamlit_app.py
# 對 markets 面板的呼叫）介面相容。
BENCHMARK_SYMBOL = "acwi.us"
BENCHMARK_LABEL = "全球股票 ACWI"

# 「全球資產」面板（v2）雙基準：ACWI ＋ 美元指數 DXY。
ACWI_SYMBOL = BENCHMARK_SYMBOL
ACWI_LABEL = BENCHMARK_LABEL
DXY_SYMBOL = "DX-Y.NYB"  # yfinance 代碼，stooq 無此指數
DXY_LABEL = "美元指數 DXY"

# (代碼, 面板顯示名) —— 同一代碼在不同面板可以有不同顯示名
# （例如 spy.us 在資產面板顯示「美股 SPY」、在市場面板顯示「美國」）。
# 這是「以 ACWI 為基準」視角下的全集（16 項，含美元 UUP）。
ASSET_PANEL: list[tuple[str, str]] = [
    ("spy.us", "美股 SPY"),
    ("qqq.us", "美股科技 QQQ"),
    ("tlt.us", "美債20年 TLT"),
    ("ief.us", "美債7-10年 IEF"),
    ("lqd.us", "投資級債 LQD"),
    ("hyg.us", "高收益債 HYG"),
    ("gld.us", "黃金 GLD"),
    ("slv.us", "白銀 SLV"),
    ("CL=F", "原油 WTI"),
    ("HG=F", "銅"),
    ("dbc.us", "商品 DBC"),
    ("vnq.us", "房地產 VNQ"),
    ("btcusd", "比特幣 BTC"),
    ("fxe.us", "歐元 FXE"),
    ("fxy.us", "日圓 FXY"),
    ("uup.us", "美元 UUP"),
]

# DXY 基準視角：排除「美元 UUP」（自己除自己無意義），其餘 15 項共用同一份定義。
_UUP_SYMBOL = "uup.us"
ASSET_PANEL_DXY: list[tuple[str, str]] = [
    (symbol, label) for symbol, label in ASSET_PANEL if symbol != _UUP_SYMBOL
]

# build_multi_benchmark_dataset() 的輸入：(基準代碼, 基準顯示名, 該基準的面板成員清單)
ASSET_BENCHMARK_SPECS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (ACWI_SYMBOL, ACWI_LABEL, ASSET_PANEL),
    (DXY_SYMBOL, DXY_LABEL, ASSET_PANEL_DXY),
]

# 資產面板預設隱藏（避免初見太擠，使用者可在清單勾回來）；兩個基準都有這些
# 成員，故同一份名單對兩個基準皆適用。
ASSET_DEFAULT_HIDDEN: list[str] = ["白銀 SLV", "投資級債 LQD", "美債7-10年 IEF", "歐元 FXE"]

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
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    benchmark_label: str = BENCHMARK_LABEL,
) -> dict:
    """單一基準版本（沿用 v1 介面，供「市場輪動」面板與外部呼叫端——例如
    streamlit_app.py——使用；新增的 benchmark_symbol/benchmark_label 兩個
    參數皆有預設值＝ACWI，舊呼叫端不需修改任何一行就能維持原行為）。
    """
    symbols_needed = [benchmark_symbol] + [s for s, _ in panel]
    symbols_present = [s for s in symbols_needed if s in pivot.columns]
    missing = sorted(set(symbols_needed) - set(symbols_present))
    if missing:
        print(f"警告: 資料集「{dataset_label}」缺少品項 {missing}，其序列將全為 null")

    if benchmark_symbol not in pivot.columns:
        print(f"錯誤: 找不到基準 {benchmark_symbol}，資料集「{dataset_label}」無法計算")
        return {
            "id": dataset_id,
            "label": dataset_label,
            "as_of": None,
            "benchmarks": [benchmark_label],
            "periods": PERIODS,
            "dates": [],
            "series": {benchmark_label: {}},
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
            "benchmarks": [benchmark_label],
            "periods": PERIODS,
            "dates": [],
            "series": {benchmark_label: {}},
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
        aligned[benchmark_symbol]
        if benchmark_symbol in aligned.columns
        else pd.Series(index=aligned.index, dtype=float)
    )

    series: dict[str, dict[str, dict[str, list]]] = {benchmark_label: {}}
    for window in PERIODS:
        period_key = str(window)
        series[benchmark_label][period_key] = {}
        for symbol, label in panel:
            if symbol not in aligned.columns:
                series[benchmark_label][period_key][label] = [None] * n_out
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
            series[benchmark_label][period_key][label] = coords

    return {
        "id": dataset_id,
        "label": dataset_label,
        "as_of": as_of,
        "benchmarks": [benchmark_label],
        "periods": PERIODS,
        "dates": output_dates,
        "series": series,
        "default_hidden": [],
    }


def build_multi_benchmark_dataset(
    pivot: pd.DataFrame,
    dataset_id: str,
    dataset_label: str,
    benchmark_specs: list[tuple[str, str, list[tuple[str, str]]]],
    default_hidden: list[str] | None = None,
) -> dict:
    """多基準版本（v2 新增，供「全球資產」面板使用：ACWI ＋ DXY 雙基準）。

    benchmark_specs：[(基準代碼, 基準顯示名, 該基準專屬的面板成員清單), ...]。
    每個基準可以有自己的一份面板成員（例如 DXY 基準排除「美元 UUP」），
    資料契約允許 series[基準A] 與 series[基準B] 底下的成員 key 集合不同。

    對齊規則沿用 build_dataset：取「所有基準符號 + 所有基準各自面板成員」的
    聯集在 global_prices.csv 出現的日期範圍，建單一工作日曆、共用同一組
    forward-fill（最多 5 個工作日），讓所有基準共用同一份 `dates`——這與
    資料契約「dataset 只有一個 dates 陣列」一致，各基準只是同一份日期軸上
    换一個分母重算 RS-Ratio/RS-Momentum。
    """
    default_hidden = list(default_hidden or [])
    benchmark_labels = [label for _, label, _ in benchmark_specs]

    all_symbols_needed: set[str] = set()
    for bm_symbol, _, panel in benchmark_specs:
        all_symbols_needed.add(bm_symbol)
        all_symbols_needed.update(s for s, _ in panel)
    symbols_present = [s for s in all_symbols_needed if s in pivot.columns]
    missing = sorted(all_symbols_needed - set(symbols_present))
    if missing:
        print(f"警告: 資料集「{dataset_label}」缺少品項 {missing}，其序列將全為 null")

    missing_benchmarks = [lbl for sym, lbl, _ in benchmark_specs if sym not in pivot.columns]
    for lbl in missing_benchmarks:
        print(f"錯誤: 找不到基準 {lbl}，資料集「{dataset_label}」該基準序列將全為 null")

    empty_result = {
        "id": dataset_id,
        "label": dataset_label,
        "as_of": None,
        "benchmarks": benchmark_labels,
        "periods": PERIODS,
        "dates": [],
        "series": {lbl: {} for lbl in benchmark_labels},
        "default_hidden": default_hidden,
    }

    if not symbols_present:
        print(f"警告: 資料集「{dataset_label}」沒有任何有效資料")
        return empty_result

    sub = pivot[symbols_present].copy()
    valid_dates = sub.dropna(how="all").index
    if len(valid_dates) == 0:
        print(f"警告: 資料集「{dataset_label}」沒有任何有效資料")
        return empty_result

    # ---- 對齊規則：全部基準符號聯集的工作日曆 + 各品項 forward-fill 上限 5 個工作日 ----
    calendar = pd.bdate_range(valid_dates.min(), valid_dates.max())
    aligned = sub.reindex(calendar)
    aligned = aligned.ffill(limit=FFILL_LIMIT_BDAYS)

    output_index = aligned.index[-MAX_DATES:]
    output_dates = [d.strftime("%Y-%m-%d") for d in output_index]
    n_out = len(output_dates)
    as_of = output_dates[-1] if output_dates else None

    series: dict[str, dict[str, dict[str, list]]] = {}
    for bm_symbol, bm_label, panel in benchmark_specs:
        series[bm_label] = {}
        benchmark_series = (
            aligned[bm_symbol]
            if bm_symbol in aligned.columns
            else pd.Series(index=aligned.index, dtype=float)
        )
        for window in PERIODS:
            period_key = str(window)
            series[bm_label][period_key] = {}
            for symbol, label in panel:
                if symbol not in aligned.columns:
                    series[bm_label][period_key][label] = [None] * n_out
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
                series[bm_label][period_key][label] = coords

    return {
        "id": dataset_id,
        "label": dataset_label,
        "as_of": as_of,
        "benchmarks": benchmark_labels,
        "periods": PERIODS,
        "dates": output_dates,
        "series": series,
        "default_hidden": default_hidden,
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
        build_multi_benchmark_dataset(pivot, "assets", "全球資產", ASSET_BENCHMARK_SPECS, ASSET_DEFAULT_HIDDEN),
        build_dataset(pivot, "markets", "市場輪動", MARKET_PANEL),
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("window.RRG_DATASETS_GLOBAL = ")
        json.dump(datasets, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    print(f"已寫入 {out_path}")
    for ds in datasets:
        per_bm = []
        for bm in ds["benchmarks"]:
            n_members = len(ds["series"].get(bm, {}).get(str(PERIODS[0]), {}))
            per_bm.append(f"{bm}={n_members}")
        print(f"  {ds['id']} ({ds['label']}): as_of={ds['as_of']}, dates={len(ds['dates'])}, members[{', '.join(per_bm)}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
