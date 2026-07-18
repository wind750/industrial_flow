#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_global.py

抓取「全球資產」與「市場輪動」兩個 RRG 面板所需的海外 ETF／指數歷史日線收盤價。

資料源：
  主：stooq 歷史 CSV 端點 https://stooq.com/q/d/l/?s={symbol}&i=d
      （一次回傳整段歷史日線，不需分批回補）
  備援：yfinance（僅在 stooq 失敗或該品項 stooq 無資料時使用）。yfinance 刻意
      不寫進 requirements.txt——本機/CI 需要時另外 `pip install yfinance`
      （GitHub Actions workflow 內已這樣做）；若環境沒裝，備援會被跳過並記錄
      警告，不會讓整支腳本掛掉。

品項清單見下方 SYMBOLS（(stooq_symbol, yfinance_symbol, 說明) 三元組）。CSV 的
`name` 欄位存 stooq 代碼（例如 "spy.us"、"btcusd"）而不是面板顯示名——同一個
品項在不同面板可能有不同顯示名（例如 spy.us 在「全球資產」面板顯示「美股
SPY」、在「市場輪動」面板顯示「美國」），顯示名的對照留給 compute_global.py
處理，fetch 端只負責把「代碼 -> 收盤價」的事實存好。

輸出 data/global_prices.csv（欄位 date,name,close，UTF-8），只保留 2018 年起的
資料。個別品項失敗會記 log 並繼續抓下一個，不會讓整批失敗。

對齊規則（工作日聯集日曆 + 各品項 forward-fill 上限 5 個工作日）留給
compute_global.py 實作，本檔只存原始收盤價，不做插補。

用法：
  python fetch_global.py             # 全量抓取（stooq 本身即回全歷史，天然冪等）
  python fetch_global.py --update    # 與全量同一路徑，僅為與 fetch_twse_sector.py
                                      # 慣例保持一致的別名
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
GLOBAL_CSV_PATH = DATA_DIR / "global_prices.csv"

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

REQUEST_INTERVAL_SEC = 1.0  # 品項之間的禮貌節流
MIN_DATE = "2018-01-01"

# (stooq_symbol, yfinance_fallback_symbol, 說明——僅供 log 使用)
# 共用基準 acwi.us 只出現一次；spy.us 同時是「全球資產」與「市場輪動」兩面板
# 的成員（面板顯示名不同），但底層只需要抓一次。
#
# stooq_symbol 為 None 代表 stooq 無此品項（v2 新增的商品期貨連續合約／
# 美元指數，stooq 沒有對應代碼），直接跳過 stooq、只打 yfinance；此時輸出
# CSV 的 name 欄位改用 yfinance_symbol 當品項鍵值（見 fetch_one／run）。
SYMBOLS: list[tuple[str | None, str, str]] = [
    ("acwi.us", "ACWI", "全球股票 ACWI（共用基準）"),
    # -- 全球資產面板 --
    ("spy.us", "SPY", "美股 SPY ／ 市場面板：美國"),
    ("qqq.us", "QQQ", "美股科技 QQQ"),
    ("tlt.us", "TLT", "美債20年 TLT"),
    ("ief.us", "IEF", "美債7-10年 IEF"),
    ("lqd.us", "LQD", "投資級債 LQD"),
    ("hyg.us", "HYG", "高收益債 HYG"),
    ("gld.us", "GLD", "黃金 GLD"),
    ("slv.us", "SLV", "白銀 SLV"),
    (None, "CL=F", "原油 WTI（連續期貨合約，僅 yfinance）"),
    (None, "HG=F", "銅（連續期貨合約，僅 yfinance）"),
    ("dbc.us", "DBC", "商品 DBC"),
    ("vnq.us", "VNQ", "房地產 VNQ"),
    ("btcusd", "BTC-USD", "比特幣 BTC"),
    ("fxe.us", "FXE", "歐元 FXE"),
    ("fxy.us", "FXY", "日圓 FXY"),
    ("uup.us", "UUP", "美元 UUP"),
    (None, "DX-Y.NYB", "美元指數 DXY（指數，僅 yfinance）"),
    # -- 市場輪動面板（spy.us 已在上方列出，不重複抓）--
    ("ewt.us", "EWT", "台灣"),
    ("ewj.us", "EWJ", "日本"),
    ("ewy.us", "EWY", "南韓"),
    ("mchi.us", "MCHI", "中國"),
    ("inda.us", "INDA", "印度"),
    ("vnm.us", "VNM", "越南"),
    ("ewz.us", "EWZ", "巴西"),
    ("ewa.us", "EWA", "澳洲"),
    ("ewu.us", "EWU", "英國"),
    ("ewg.us", "EWG", "德國"),
    ("vgk.us", "VGK", "歐洲"),
]


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fetch_stooq(symbol: str) -> pd.DataFrame | None:
    """打 stooq 歷史 CSV 端點，回傳 date/close 兩欄的 DataFrame，失敗回 None。"""
    url = STOOQ_URL.format(symbol=symbol)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        text = resp.text
    except Exception as e:  # noqa: BLE001
        log(f"  stooq 請求失敗: {e}")
        return None

    if not text or "Date,Open,High,Low,Close" not in text:
        # stooq 對不存在/無資料的代碼會回 "No data" 純文字或空表頭
        log(f"  stooq 無資料（回應非預期格式，前 60 字元: {text[:60]!r}）")
        return None

    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception as e:  # noqa: BLE001
        log(f"  stooq CSV 解析失敗: {e}")
        return None

    if df.empty or "Date" not in df.columns or "Close" not in df.columns:
        log("  stooq 回傳表格為空或缺少必要欄位")
        return None

    out = df[["Date", "Close"]].rename(columns={"Date": "date", "Close": "close"})
    out = out.dropna(subset=["close"])
    if out.empty:
        return None
    return out


def fetch_yfinance(symbol: str) -> pd.DataFrame | None:
    """備援：用 yfinance 抓全歷史日線。僅在本機/CI 有裝 yfinance 時可用。"""
    try:
        import yfinance as yf
    except ImportError:
        log("  yfinance 未安裝，跳過備援（CI 環境會另外 pip install yfinance）")
        return None

    try:
        hist = yf.Ticker(symbol).history(period="max", interval="1d", auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        log(f"  yfinance 請求失敗: {e}")
        return None

    if hist is None or hist.empty or "Close" not in hist.columns:
        log("  yfinance 回傳資料為空")
        return None

    out = hist.reset_index()[["Date", "Close"]].rename(columns={"Date": "date", "Close": "close"})
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out = out.dropna(subset=["close"])
    if out.empty:
        return None
    return out


def fetch_one(stooq_symbol: str | None, yf_symbol: str, note: str) -> pd.DataFrame | None:
    key = stooq_symbol or yf_symbol
    if stooq_symbol:
        log(f"{stooq_symbol} ({note}): 嘗試 stooq...")
        df = fetch_stooq(stooq_symbol)
        if df is not None and not df.empty:
            log(f"  stooq 成功，{len(df)} 列")
            return df
        log(f"  stooq 失敗或無資料，改用 yfinance 備援 ({yf_symbol})...")
    else:
        log(f"{yf_symbol} ({note}): stooq 無此品項，直接用 yfinance...")

    df = fetch_yfinance(yf_symbol)
    if df is not None and not df.empty:
        log(f"  yfinance{'備援' if stooq_symbol else ''}成功，{len(df)} 列")
        return df

    log(f"  {key}: 主源與備援皆失敗，此品項本次略過" if stooq_symbol else f"  {key}: yfinance 失敗，此品項本次略過")
    return None


def run(symbols: list[tuple[str | None, str, str]]) -> pd.DataFrame:
    rows = []
    failed = []
    for i, (stooq_symbol, yf_symbol, note) in enumerate(symbols):
        key = stooq_symbol or yf_symbol
        df = fetch_one(stooq_symbol, yf_symbol, note)
        if df is None:
            failed.append(key)
        else:
            df = df[df["date"] >= MIN_DATE]
            for _, r in df.iterrows():
                rows.append({"date": r["date"], "name": key, "close": float(r["close"])})
        if i < len(symbols) - 1:
            time.sleep(REQUEST_INTERVAL_SEC)

    out = pd.DataFrame(rows, columns=["date", "name", "close"])
    out = out.sort_values(["date", "name"]).reset_index(drop=True)

    log("=" * 60)
    log(f"完成: {len(symbols) - len(failed)}/{len(symbols)} 個品項成功")
    if failed:
        log(f"失敗品項: {failed}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取全球資產／市場輪動面板歷史日線收盤價")
    parser.add_argument(
        "--update",
        action="store_true",
        help="與全量抓取同一路徑（stooq 本來就整段回歷史，此旗標僅為介面一致性保留）",
    )
    parser.parse_args()

    df = run(SYMBOLS)
    if df.empty:
        print("錯誤: 所有品項皆抓取失敗，未寫出 global_prices.csv", file=sys.stderr)
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(GLOBAL_CSV_PATH, index=False, encoding="utf-8")
    log(f"已寫入 {GLOBAL_CSV_PATH} ({len(df)} 列, {df['name'].nunique()} 個品項)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
