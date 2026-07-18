#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_rrg.py

讀 data/sector_indices.csv，對兩個基準（發行量加權股價指數、臺灣50指數）、
三個週期 (20/60/120) 各算一組 RRG (Relative Rotation Graph) 座標，
輸出 web/rrg_data.js 供前端讀取。

公式（公開近似，非 JdK 原版）：
  rs           = 100 * sector_close / benchmark_close
  rs_ratio_raw = 100 + (rs  - rolling_mean(rs,  W)) / rolling_std(rs,  W)
  rs_ratio     = trailing_wma(rs_ratio_raw, SMOOTH_WINDOW)
  mom          = rs_ratio.pct_change(5) * 100        # 吃平滑後的 rs_ratio
  mom_raw      = 100 + (mom - rolling_mean(mom, W)) / rolling_std(mom, W)
  rs_momentum  = trailing_wma(mom_raw, SMOOTH_WINDOW)

trailing_wma 是只用過去資料的線性加權移動平均（權重 1..SMOOTH_WINDOW，最新最重、
min_periods=1），純粹平滑視覺鋸齒，不引入未來資訊——細節見 README.md。

用法：
  python compute_rrg.py                       # 輸出到預設 web/rrg_data.js
  python compute_rrg.py --out data\\_smoke_rrg_data.js
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
SECTOR_CSV_PATH = DATA_DIR / "sector_indices.csv"
DEFAULT_OUT_PATH = SCRIPT_DIR / "web" / "rrg_data.js"

BENCHMARKS = ["發行量加權股價指數", "臺灣50指數"]
PERIODS = [20, 60, 120]
MAX_DATES = 240
MOM_LOOKBACK = 5

# RS-Ratio / RS-Momentum 平滑層視窗（trailing 線性加權移動平均）。
# 設 0 或 1 即停用平滑（回到未平滑的原始 zscore 序列）。
SMOOTH_WINDOW = 4

DEFAULT_HIDDEN = [
    "水泥窯製類指數",
    "塑膠化工類指數",
    "機電類指數",
    "化學生技醫療類指數",
    "電子工業類指數",
]


def clean_float(v: float) -> float | None:
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return round(float(v), 2)


def trailing_wma(s: pd.Series, window: int) -> pd.Series:
    """Trailing 線性加權移動平均（權重 1..window，最新的觀測值權重最高）。

    只使用視窗內「過去到當下」的資料（pandas rolling 預設右對齊、非
    center=True），對任一時點 t 的輸出只依賴 t 及更早的輸入，不構成前視
    （lookahead）。min_periods=1：視窗尚未填滿時（例如序列開頭）改用當下
    可取得的較短視窗，權重相應縮短為 1..n，同樣維持「最新最重」且不觸及
    未來值。

    window <= 1（含 0）時視為停用平滑，原樣傳回輸入序列。
    """
    if not window or window <= 1:
        return s

    def _wma(x: np.ndarray) -> float:
        n = len(x)
        w = np.arange(1, n + 1, dtype=float)
        return float(np.dot(x, w) / w.sum())

    return s.rolling(window=window, min_periods=1).apply(_wma, raw=True)


def compute_rs_ratio_momentum(rs: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    rolling_mean = rs.rolling(window).mean()
    rolling_std = rs.rolling(window).std()
    rs_ratio = 100 + (rs - rolling_mean) / rolling_std
    rs_ratio = trailing_wma(rs_ratio, SMOOTH_WINDOW)  # 平滑層：鏈路下游 mom 吃平滑後的值

    mom = rs_ratio.pct_change(MOM_LOOKBACK) * 100
    mom_mean = mom.rolling(window).mean()
    mom_std = mom.rolling(window).std()
    rs_momentum = 100 + (mom - mom_mean) / mom_std
    rs_momentum = trailing_wma(rs_momentum, SMOOTH_WINDOW)

    return rs_ratio, rs_momentum


def main() -> int:
    parser = argparse.ArgumentParser(description="計算 RRG 座標並輸出 web/rrg_data.js")
    parser.add_argument("--out", type=str, default=None, help="輸出路徑（預設 web/rrg_data.js）")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else DEFAULT_OUT_PATH
    if not out_path.is_absolute():
        out_path = SCRIPT_DIR / out_path

    if not SECTOR_CSV_PATH.exists():
        print(f"錯誤: 找不到 {SECTOR_CSV_PATH}，請先執行 fetch_twse_sector.py")
        return 1

    df = pd.read_csv(SECTOR_CSV_PATH, encoding="utf-8")
    if df.empty:
        print(f"錯誤: {SECTOR_CSV_PATH} 沒有資料")
        return 1

    pivot = df.pivot_table(index="date", columns="name", values="close", aggfunc="last")
    pivot = pivot.sort_index()

    all_dates = list(pivot.index)
    as_of = all_dates[-1]

    sector_names = sorted(c for c in pivot.columns if c.endswith("類指數"))

    output_dates = all_dates[-MAX_DATES:]
    n_out = len(output_dates)

    series: dict[str, dict[str, dict[str, list]]] = {}

    for benchmark in BENCHMARKS:
        if benchmark not in pivot.columns:
            print(f"警告: 找不到基準指數「{benchmark}」，其序列將全為 null")
        series[benchmark] = {}
        benchmark_series = pivot[benchmark] if benchmark in pivot.columns else pd.Series(
            index=pivot.index, dtype=float
        )

        for window in PERIODS:
            period_key = str(window)
            series[benchmark][period_key] = {}

            for sector in sector_names:
                sector_series = pivot[sector]
                rs = 100 * sector_series / benchmark_series
                rs_ratio, rs_momentum = compute_rs_ratio_momentum(rs, window)

                rs_ratio_out = rs_ratio.reindex(output_dates)
                rs_momentum_out = rs_momentum.reindex(output_dates)

                coords = []
                for i in range(n_out):
                    x = clean_float(rs_ratio_out.iloc[i])
                    y = clean_float(rs_momentum_out.iloc[i])
                    if x is None or y is None:
                        coords.append(None)
                    else:
                        coords.append([x, y])

                series[benchmark][period_key][sector] = coords

    result = {
        "as_of": as_of,
        "benchmarks": BENCHMARKS,
        "periods": PERIODS,
        "dates": output_dates,
        "series": series,
        "default_hidden": DEFAULT_HIDDEN,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("window.RRG_DATA = ")
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    print(f"已寫入 {out_path}")
    print(f"as_of={as_of}, dates={n_out}, sectors={len(sector_names)}, benchmarks={BENCHMARKS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
