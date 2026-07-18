#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_tpex_sector.py

從證交所（櫃買中心）公開 API 抓取每日上櫃類股價格指數收盤值，存成：
  - data/raw_tpex/{YYYYMMDD}.json        原始回應（僅在偵測為交易日時存檔）
  - data/tpex_non_trading_days.json      已知非交易日清單（之後回補會直接跳過，不重打）
  - data/tpex_indices.csv                彙整後的長表格 (date, name, close)

資料源：
  https://www.tpex.org.tw/www/zh-tw/afterTrading/indexSummary?date={YYYY/MM/DD}&response=json

注意事項（2026-07-18 實測確認）：
  - 日期參數只吃西元斜線格式 YYYY/MM/DD（民國或 YYYYMMDD 會回 {"stat":"日期錯誤"}）。
  - 回應一定含兩個表格：tables[0] 是「上櫃股價指數收盤行情」（價格指數，欄位第一
    個是「指數」），tables[1] 是報酬指數（欄位第一個是「報酬指數」）。本腳本只取
    tables[0]。判斷用 title 字串比對，title 在各年度資料中皆穩定為
    "上櫃股價指數收盤行情"。
  - 非交易日：stat 仍是 "ok"，但 tables[0]["data"] 是空陣列（totalCount=0）。與
    證交所 API（非交易日靠 stat != "OK" 判斷）不同，這裡要靠 rows 是否為空判斷。
  - 產業檔數隨年份增加（2016 約 24 檔 → 2025+ 約 34 檔總表列數，含主題/市場指數），
    彙整時必須以「指數」欄的名稱字串對齊，不可假設固定列位。
  - 不需要 User-Agent header、不需要停用 SSL 驗證（憑證正常）。
  - 「收市指數」欄位是含千分位逗號的字串，需去逗號轉 float。

用法：
  python fetch_tpex_sector.py --start 2026-07-10 --end 2026-07-16   # 回補區間
  python fetch_tpex_sector.py --update                              # 只抓今天
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
RAW_DIR = DATA_DIR / "raw_tpex"
NON_TRADING_DAYS_PATH = DATA_DIR / "tpex_non_trading_days.json"
TPEX_CSV_PATH = DATA_DIR / "tpex_indices.csv"

TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/indexSummary?date={ymd}&response=json"

# tables[0] 的 title（價格指數表；tables[1] 是報酬指數表，title 為空字串）
PRICE_TABLE_TITLE = "上櫃股價指數收盤行情"

REQUEST_INTERVAL_SEC = 0.7  # >= 600ms 節流要求，留一點餘裕
RETRY_WAIT_SEC = 5


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_non_trading_days() -> set[str]:
    if not NON_TRADING_DAYS_PATH.exists():
        return set()
    with open(NON_TRADING_DAYS_PATH, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return set()
    if isinstance(data, list):
        return set(data)
    if isinstance(data, dict) and "dates" in data:
        return set(data["dates"])
    return set()


def save_non_trading_days(days: set[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(NON_TRADING_DAYS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(days), f, ensure_ascii=False, indent=2)


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def daterange(start: date, end: date):
    d = start
    one_day = timedelta(days=1)
    while d <= end:
        yield d
        d += one_day


def fetch_one_day(d: date) -> dict | None:
    """實際打 API，含 1 次重試。回傳 JSON dict 或 None（徹底失敗）。"""
    ymd = d.strftime("%Y/%m/%d")
    url = TPEX_URL.format(ymd=ymd)
    last_err = None
    for attempt in (1, 2):
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data
        except Exception as e:  # noqa: BLE001 - 任何失敗都重試/記錄
            last_err = e
            log(f"  attempt {attempt} failed for {d.isoformat()}: {e}")
            if attempt == 1:
                time.sleep(RETRY_WAIT_SEC)
    log(f"  giving up on {d.isoformat()} after 2 attempts ({last_err})")
    return None


def find_price_table(data: dict) -> dict | None:
    for table in data.get("tables") or []:
        if table.get("title") == PRICE_TABLE_TITLE:
            return table
    # fallback：title 比對失敗時，用欄位第一個是否為「指數」判斷（排除報酬指數表）
    for table in data.get("tables") or []:
        fields = table.get("fields") or []
        if fields and fields[0] == "指數":
            return table
    return None


def process_dates(dates: list[date]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    non_trading_days = load_non_trading_days()

    for d in dates:
        iso = d.isoformat()

        if d.weekday() >= 5:  # 5=Sat, 6=Sun
            log(f"{iso}: 週末，跳過")
            continue

        ymd = d.strftime("%Y%m%d")
        raw_path = RAW_DIR / f"{ymd}.json"

        if raw_path.exists():
            log(f"{iso}: raw 已存在，跳過")
            continue

        if iso in non_trading_days:
            log(f"{iso}: 已知非交易日，跳過")
            continue

        log(f"{iso}: 抓取中...")
        data = fetch_one_day(d)
        # 不論成功失敗，兩個請求之間都保留節流間隔
        time.sleep(REQUEST_INTERVAL_SEC)

        if data is None:
            log(f"{iso}: 抓取失敗，略過（未記入 non_trading_days，之後可重試）")
            continue

        if data.get("stat") != "ok":
            log(f"{iso}: stat={data.get('stat')!r}，視為異常，略過（未記入 non_trading_days）")
            continue

        table = find_price_table(data)
        if table is None:
            log(f"{iso}: 找不到「{PRICE_TABLE_TITLE}」表格，略過")
            continue

        rows = table.get("data") or []
        if len(rows) == 0:
            log(f"{iso}: stat=ok 但 rows=0，視為非交易日")
            non_trading_days.add(iso)
            save_non_trading_days(non_trading_days)
            continue

        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        log(f"{iso}: 已存 {raw_path.name}（{len(rows)} 列）")


def parse_close(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if s == "" or s == "--":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def aggregate_csv() -> pd.DataFrame:
    rows = []
    for raw_path in sorted(RAW_DIR.glob("*.json")):
        ymd = raw_path.stem
        try:
            d = datetime.strptime(ymd, "%Y%m%d").date()
        except ValueError:
            log(f"跳過無法解析檔名的檔案: {raw_path.name}")
            continue
        iso = d.isoformat()

        with open(raw_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        table = find_price_table(data)
        if table is None:
            log(f"{iso}: 找不到「{PRICE_TABLE_TITLE}」表格，跳過")
            continue

        for row in table.get("data") or []:
            if not row:
                continue
            name = row[0]
            close = parse_close(row[1]) if len(row) > 1 else None
            if close is None:
                continue
            rows.append({"date": iso, "name": name, "close": close})

    df = pd.DataFrame(rows, columns=["date", "name", "close"])
    df = df.sort_values(["date", "name"]).reset_index(drop=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(TPEX_CSV_PATH, index=False, encoding="utf-8")
    log(f"已寫入 {TPEX_CSV_PATH} ({len(df)} 列)")
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取上櫃（櫃買中心）類股價格指數")
    parser.add_argument("--start", type=str, help="回補起始日 YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="回補結束日 YYYY-MM-DD")
    parser.add_argument("--update", action="store_true", help="只抓今天")
    args = parser.parse_args()

    if args.update:
        dates = [date.today()]
    elif args.start and args.end:
        start = parse_date(args.start)
        end = parse_date(args.end)
        if start > end:
            print("錯誤: --start 不可晚於 --end", file=sys.stderr)
            return 1
        dates = list(daterange(start, end))
    else:
        print("錯誤: 需指定 --update 或 (--start 與 --end)", file=sys.stderr)
        return 1

    process_dates(dates)
    aggregate_csv()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
