#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_tpex.py

讀 data/tpex_indices.csv，以「櫃買指數」為單一基準，對三個週期 (20/60/120)
各算一組 RRG (Relative Rotation Graph) 座標，輸出 web/rrg_data_tpex.js 供前端讀取。

RS-Ratio / RS-Momentum 計算公式與平滑層直接複用 compute_rrg.py 的
compute_rs_ratio_momentum() / clean_float()（唯讀 import，不修改該檔）。

成員＝上櫃 22 個產業類股中，CSV 實際有資料者（不含富櫃50/200、公司治理、ESG、
櫃買總指數等主題/市場指數）。

用法：
  python compute_tpex.py                            # 輸出到預設 web/rrg_data_tpex.js
  python compute_tpex.py --out data\\_smoke_rrg_data_tpex.js
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from compute_rrg import clean_float, compute_rs_ratio_momentum

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
TPEX_CSV_PATH = DATA_DIR / "tpex_indices.csv"
DEFAULT_OUT_PATH = SCRIPT_DIR / "web" / "rrg_data_tpex.js"

DATASET_ID = "tpex"
DATASET_LABEL = "上櫃類股"

BENCHMARK = "櫃買指數"
PERIODS = [20, 60, 120]
MAX_DATES = 240

# 上櫃 22 個產業類股（不含富櫃50/200、公司治理、ESG、櫃買總指數等主題/市場指數）
TPEX_SECTORS = [
    "紡織纖維",
    "電機機械",
    "鋼鐵工業",
    "電子工業",
    "建材營造",
    "航運業",
    "觀光餐旅",
    "其他",
    "化學工業",
    "生技醫療",
    "半導體業",
    "電腦及週邊設備業",
    "光電業",
    "通信網路業",
    "電子零組件業",
    "電子通路業",
    "資訊服務業",
    "其他電子業",
    "文化創意業",
    "綠能環保",
    "數位雲端",
    "居家生活",
]

DEFAULT_HIDDEN: list[str] = []


def load_tpex_pivot() -> pd.DataFrame:
    """讀 data/tpex_indices.csv，轉成 date x name 的寬表格，index 為
    DatetimeIndex（比照 compute_global.py 的 load_pivot 慣例）。CSV 不存在
    或為空時回傳空 DataFrame，由呼叫端決定如何處理（CLI 視為錯誤；
    streamlit_app.py 視為「上櫃資料尚未就緒」，略過上櫃面板但頁面照常）。
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


def build_tpex_dataset(
    pivot: pd.DataFrame,
    dataset_id: str = DATASET_ID,
    dataset_label: str = DATASET_LABEL,
) -> dict:
    """組出上櫃 RRG dataset dict（{id, label, as_of, benchmarks, periods,
    dates, series, default_hidden}），供 web/rrg_data_tpex.js（本檔 main()）
    與 streamlit_app.py 共用。實際運算全部呼叫 compute_rrg.py 的
    compute_rs_ratio_momentum／clean_float，本函式只負責迴圈組裝。

    `pivot` 需為 date x name 寬表格，index 可為字串或 DatetimeIndex（本函式
    內部一律 pd.to_datetime 正規化後再排序，與 compute_global.build_dataset
    的呼叫慣例一致）。pivot 為空或缺基準欄位時回傳 dates=[] 的空殼 dict，
    不拋例外，方便呼叫端判斷是否要嵌入這個面板。
    """
    if pivot.empty or BENCHMARK not in pivot.columns:
        if not pivot.empty:
            print(f"錯誤: 找不到基準指數「{BENCHMARK}」，資料集「{dataset_label}」無法計算")
        return {
            "id": dataset_id,
            "label": dataset_label,
            "as_of": None,
            "benchmarks": [BENCHMARK],
            "periods": PERIODS,
            "dates": [],
            "series": {BENCHMARK: {}},
            "default_hidden": DEFAULT_HIDDEN,
        }

    pivot = pivot.copy()
    pivot.index = pd.to_datetime(pivot.index)
    pivot = pivot.sort_index()

    all_dates = list(pivot.index)

    sector_names = [s for s in TPEX_SECTORS if s in pivot.columns]
    missing = [s for s in TPEX_SECTORS if s not in pivot.columns]
    if missing:
        print(f"警告: CSV 中找不到以下產業類股，將略過: {missing}")

    output_index = all_dates[-MAX_DATES:]
    output_dates = [d.strftime("%Y-%m-%d") for d in output_index]
    n_out = len(output_dates)
    as_of = output_dates[-1] if output_dates else None

    benchmark_series = pivot[BENCHMARK]

    series: dict[str, dict[str, dict[str, list]]] = {BENCHMARK: {}}

    for window in PERIODS:
        period_key = str(window)
        series[BENCHMARK][period_key] = {}

        for sector in sector_names:
            sector_series = pivot[sector]
            rs = 100 * sector_series / benchmark_series
            rs_ratio, rs_momentum = compute_rs_ratio_momentum(rs, window)

            rs_ratio_out = rs_ratio.reindex(output_index)
            rs_momentum_out = rs_momentum.reindex(output_index)

            coords = []
            for i in range(n_out):
                x = clean_float(rs_ratio_out.iloc[i])
                y = clean_float(rs_momentum_out.iloc[i])
                if x is None or y is None:
                    coords.append(None)
                else:
                    coords.append([x, y])

            series[BENCHMARK][period_key][sector] = coords

    return {
        "id": dataset_id,
        "label": dataset_label,
        "as_of": as_of,
        "benchmarks": [BENCHMARK],
        "periods": PERIODS,
        "dates": output_dates,
        "series": series,
        "default_hidden": DEFAULT_HIDDEN,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="計算上櫃 RRG 座標並輸出 web/rrg_data_tpex.js")
    parser.add_argument("--out", type=str, default=None, help="輸出路徑（預設 web/rrg_data_tpex.js）")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else DEFAULT_OUT_PATH
    if not out_path.is_absolute():
        out_path = SCRIPT_DIR / out_path

    if not TPEX_CSV_PATH.exists():
        print(f"錯誤: 找不到 {TPEX_CSV_PATH}，請先執行 fetch_tpex_sector.py")
        return 1

    pivot = load_tpex_pivot()
    if pivot.empty:
        print(f"錯誤: {TPEX_CSV_PATH} 沒有資料")
        return 1

    if BENCHMARK not in pivot.columns:
        print(f"錯誤: 找不到基準指數「{BENCHMARK}」")
        return 1

    result = build_tpex_dataset(pivot)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("window.RRG_DATASET_TPEX = ")
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    n_out = len(result["dates"])
    n_sectors = len(result["series"].get(BENCHMARK, {}).get(str(PERIODS[0]), {}))
    print(f"已寫入 {out_path}")
    print(f"as_of={result['as_of']}, dates={n_out}, sectors={n_sectors}, benchmark={BENCHMARK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
