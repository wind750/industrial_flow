#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compute_stocks.py

「強產業內選強個股」排行計算。讀：
  - data/stock_prices.csv    個股日收盤（date,market,code,name,close,amount）
  - data/industry_map.csv    個股 → 產業別 → 對映的類股指數名稱
  - data/sector_indices.csv  上市類股指數收盤（compute_rrg.py 的資料源）
  - data/tpex_indices.csv    上櫃類股指數收盤（compute_tpex.py 的資料源）

對每個「有個股對映的產業」（上市細分類＋上櫃 22 類），產業內每檔個股：

  rs = 100 * stock_close / sector_index_close   （日期對齊 inner join）

用 compute_rrg.py 既有的 compute_rs_ratio_momentum(rs, window)（含 trailing
WMA 平滑，無前視）算 W=20/60/120 的 (x, y) 座標，x 即 RS-Ratio。

流動性過濾：個股 20 日均成交金額 >= LIQUIDITY_FLOOR_AMOUNT（模組常數，
預設 3,000 萬元）才入榜。

雙強定義：個股最新 x >= 100（相對自己產業指數轉強）。每產業每週期依 x
降冪取 top 10，不足 10 檔就有幾檔列幾檔。

輸出：
  - web/rrg_data_stocks.js  window.RRG_STOCK_RANKINGS = {...};（前端用，未進版控）
  - data/stock_rankings.json  同內容純 JSON（Streamlit 用，進版控）

用法：
  python compute_stocks.py
  python compute_stocks.py --as-of 2026-06-01   # 只用該日期(含)以前的資料計算
                                                  # ——無前視驗證：與全量計算在
                                                  # 該日的座標應完全一致。
  python compute_stocks.py --out-js data\\_smoke_rrg_data_stocks.js --out-json data\\_smoke_stock_rankings.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from compute_rrg import clean_float, compute_rs_ratio_momentum

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"

STOCK_PRICES_CSV = DATA_DIR / "stock_prices.csv"
INDUSTRY_MAP_CSV = DATA_DIR / "industry_map.csv"
SECTOR_CSV = DATA_DIR / "sector_indices.csv"
TPEX_SECTOR_CSV = DATA_DIR / "tpex_indices.csv"

DEFAULT_OUT_JS = SCRIPT_DIR / "web" / "rrg_data_stocks.js"
DEFAULT_OUT_JSON = DATA_DIR / "stock_rankings.json"

PERIODS = [20, 60, 120]

# 流動性門檻：20 日均成交金額（元）。模組常數，可依需要調整。
LIQUIDITY_FLOOR_AMOUNT = 30_000_000

# 雙強門檻：最新 RS-Ratio（x）>= 此值才視為「強於自己產業指數」
STRONG_X_THRESHOLD = 100.0

TOP_N = 10


def log(msg: str) -> None:
    print(msg, flush=True)


def load_stock_prices(as_of: str | None) -> pd.DataFrame:
    df = pd.read_csv(STOCK_PRICES_CSV, encoding="utf-8", dtype={"code": str})
    if as_of:
        df = df[df["date"] <= as_of]
    return df


def load_sector_pivot(path: Path, as_of: str | None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, encoding="utf-8")
    if as_of:
        df = df[df["date"] <= as_of]
    pivot = df.pivot_table(index="date", columns="name", values="close", aggfunc="last")
    return pivot.sort_index()


def load_industry_map() -> pd.DataFrame:
    df = pd.read_csv(INDUSTRY_MAP_CSV, encoding="utf-8", dtype={"code": str})
    return df[df["sector_index_name"] != "unmapped"].copy()


def compute_market_panel(
    market: str,
    stock_df: pd.DataFrame,
    sector_pivot: pd.DataFrame,
    industry_map: pd.DataFrame,
) -> dict:
    panel: dict[str, dict[str, list]] = {}

    market_stocks = stock_df[stock_df["market"] == market]
    if market_stocks.empty or sector_pivot.empty:
        return panel

    close_pivot = market_stocks.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    amount_pivot = market_stocks.pivot_table(index="date", columns="code", values="amount", aggfunc="last").sort_index()
    name_by_code = market_stocks.drop_duplicates("code", keep="last").set_index("code")["name"].to_dict()

    market_map = industry_map[industry_map["market"] == market]
    sectors = sorted(market_map["sector_index_name"].unique())

    for sector_name in sectors:
        if sector_name not in sector_pivot.columns:
            log(f"警告: [{market}] 對映到的類股指數「{sector_name}」不在指數資料中，跳過")
            continue

        codes = sorted(
            c for c in market_map.loc[market_map["sector_index_name"] == sector_name, "code"].unique()
            if c in close_pivot.columns
        )
        if not codes:
            continue

        sector_series = sector_pivot[sector_name]
        panel[sector_name] = {str(w): [] for w in PERIODS}

        candidates: dict[int, list[dict]] = {w: [] for w in PERIODS}

        for code in codes:
            stock_close = close_pivot[code]
            stock_amount = amount_pivot[code] if code in amount_pivot.columns else pd.Series(dtype=float)

            common_index = stock_close.dropna().index.intersection(sector_series.dropna().index)
            if len(common_index) < 2:
                continue
            common_index = common_index.sort_values()

            stock_close_aligned = stock_close.reindex(common_index)
            sector_close_aligned = sector_series.reindex(common_index)
            amount_aligned = stock_amount.reindex(common_index)

            rs = 100 * stock_close_aligned / sector_close_aligned
            amt20_series = amount_aligned.rolling(window=20, min_periods=20).mean()
            amt20_latest = amt20_series.iloc[-1] if len(amt20_series) else None

            if amt20_latest is None or pd.isna(amt20_latest) or amt20_latest < LIQUIDITY_FLOOR_AMOUNT:
                continue

            for window in PERIODS:
                rs_ratio, rs_momentum = compute_rs_ratio_momentum(rs, window)
                if len(rs_ratio) == 0:
                    continue
                x = clean_float(rs_ratio.iloc[-1])
                y = clean_float(rs_momentum.iloc[-1])
                if x is None or y is None:
                    continue
                if x < STRONG_X_THRESHOLD:
                    continue
                candidates[window].append(
                    {
                        "code": code,
                        "name": name_by_code.get(code, ""),
                        "x": x,
                        "y": y,
                        "amt20": int(round(amt20_latest)),
                    }
                )

        for window in PERIODS:
            ranked = sorted(candidates[window], key=lambda r: -r["x"])[:TOP_N]
            panel[sector_name][str(window)] = ranked

    return panel


def main() -> int:
    parser = argparse.ArgumentParser(description="計算產業內個股雙強排行")
    parser.add_argument("--as-of", type=str, default=None, help="只用此日期(含)以前的資料計算（YYYY-MM-DD），用於無前視驗證")
    parser.add_argument("--out-js", type=str, default=None, help="輸出路徑（預設 web/rrg_data_stocks.js）")
    parser.add_argument("--out-json", type=str, default=None, help="輸出路徑（預設 data/stock_rankings.json）")
    args = parser.parse_args()

    out_js = Path(args.out_js) if args.out_js else DEFAULT_OUT_JS
    if not out_js.is_absolute():
        out_js = SCRIPT_DIR / out_js
    out_json = Path(args.out_json) if args.out_json else DEFAULT_OUT_JSON
    if not out_json.is_absolute():
        out_json = SCRIPT_DIR / out_json

    if not STOCK_PRICES_CSV.exists():
        print(f"錯誤: 找不到 {STOCK_PRICES_CSV}，請先執行 fetch_stocks.py")
        return 1
    if not INDUSTRY_MAP_CSV.exists():
        print(f"錯誤: 找不到 {INDUSTRY_MAP_CSV}，請先執行 fetch_industry_map.py")
        return 1

    stock_df = load_stock_prices(args.as_of)
    if stock_df.empty:
        print("錯誤: stock_prices.csv 在指定日期範圍內沒有資料")
        return 1

    twse_sector_pivot = load_sector_pivot(SECTOR_CSV, args.as_of)
    tpex_sector_pivot = load_sector_pivot(TPEX_SECTOR_CSV, args.as_of)
    industry_map = load_industry_map()

    as_of = str(stock_df["date"].max())

    panels = {}
    twse_panel = compute_market_panel("twse", stock_df, twse_sector_pivot, industry_map)
    if twse_panel:
        panels["twse"] = twse_panel
    tpex_panel = compute_market_panel("tpex", stock_df, tpex_sector_pivot, industry_map)
    if tpex_panel:
        panels["tpex"] = tpex_panel

    result = {
        "as_of": as_of,
        "liquidity_floor": LIQUIDITY_FLOOR_AMOUNT,
        "panels": panels,
    }

    out_js.parent.mkdir(parents=True, exist_ok=True)
    with open(out_js, "w", encoding="utf-8") as f:
        f.write("window.RRG_STOCK_RANKINGS = ")
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    n_sectors = sum(len(v) for v in panels.values())
    payload_bytes = out_json.stat().st_size
    log(f"已寫入 {out_js}")
    log(f"已寫入 {out_json}")
    log(f"as_of={as_of}, markets={list(panels.keys())}, sectors_with_stocks={n_sectors}, payload={payload_bytes} bytes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
