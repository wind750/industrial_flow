#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_stocks.py

抓取「上市＋上櫃全市場個股」日收盤，供產業內雙強排行（compute_stocks.py）使用。
存成：
  - data/raw_stocks/twse/{YYYYMMDD}.json      TWSE 原始回應（僅在 stat == "OK" 時存檔）
  - data/raw_stocks/tpex/{YYYYMMDD}.json      TPEx 原始回應（僅在偵測為交易日時存檔）
  - data/raw_stocks/twse_non_trading_days.json  已知 TWSE 非交易日清單
  - data/raw_stocks/tpex_non_trading_days.json  已知 TPEx 非交易日清單
  - data/stock_prices.csv                      彙整後長表格（滾動最近 300 個交易日）

資料源（2026-07-18 實測確認）：

  TWSE 全部個股日收盤：
    https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={YYYYMMDD}&type=ALLBUT0999&response=json
    回應含多個 tables，個股表 title 形如「115年07月17日 每日收盤行情(全部(不含權證、
    牛熊證、可展延牛熊證))」，欄位：證券代號,證券名稱,成交股數,成交筆數,成交金額,
    開盤價,最高價,最低價,收盤價,漲跌(+/-),漲跌價差,最後揭示買價,最後揭示買量,
    最後揭示賣價,最後揭示賣量,本益比。非交易日 stat != "OK"（與 fetch_twse_sector.py
    一致）。實測 2026-07-17 該表共 1371 列（含 ETF/牛熊證代碼），純 4 碼 1xxx-9xxx
    個股約 1083 檔。

  TPEx 全部個股日收盤：
    https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={ROC YYY/MM/DD}&id=&response=json
    日期參數吃「民國年/MM/DD」（例如 115/07/17），與 fetch_tpex_sector.py 的
    indexSummary 端點（西元斜線）不同，需另外轉換。回應 tables[0] title「上櫃股票
    行情」，欄位：代號,名稱,收盤,漲跌,開盤,最高,最低,均價,成交股數,成交金額(元),
    成交筆數,最後買價,最後買量(張數),最後賣價,最後賣量(張數),發行股數,次日 參考價,
    次日 漲停價,次日 跌停價。tables[1]「管理股票」欄位相同、一併納入（受處置的
    上櫃股仍是普通股）。非交易日：stat 仍為 "ok"，但找不到符合欄位的表格或表格
    data 為空陣列。實測 2026-07-17 tables[0] 共 10012 列，其中純 4 碼 1xxx-9xxx
    個股 889 檔（其餘為 6 碼公司債/可轉債與 00 開頭 ETF/受益證券，被下方過濾規則
    排除）。SSL：本機實測會間歇性拋出
    `SSLCertVerificationError: Missing Subject Key Identifier`（Windows 對
    tpex.org.tw 憑證鏈結的嚴格驗證問題，非請求內容錯誤），與另一子專案
    艾略特波浪架構/資金流向/tide_sector_flow.py 遇到的狀況一致，沿用同一種
    解法：自訂 HTTPAdapter，關閉 `ssl.VERIFY_X509_STRICT` 旗標（僅放寬這一項
    嚴格性檢查，憑證本身仍會驗證，不是 verify=False 完全關閉 SSL 驗證）。

  已知資料缺口（非本腳本 bug，實測記錄）：2026-07-10 該日 TWSE「價格指數」
  (type=IND) 端點回應正常（sector_indices.csv 有資料），但「每日收盤行情」
  (type=ALLBUT0999) 端點對該日連續多次請求都回傳
  stat="很抱歉，沒有符合條件的資料!"，鄰近日期（07-08/07-09/07-13起）皆正常。
  判斷為 TWSE 該報表在該日的資料缺口，非週末/國定假日（不是真正的非交易日），
  但行為上仍會被本腳本記入 twse_non_trading_days.json（避免無限重試）；如需
  強制重試，手動從該檔案移除該日期即可。

普通股過濾規則（彙整成 stock_prices.csv 時套用）：
  - 只保留代號為「4 碼純數字、首碼 1-9」者（正則 ^[1-9][0-9]{3}$）。
  - 排除 00 開頭（ETF、受益證券、主動式ETF等，如 0050、00679B）。
  - 排除 5/6 碼代碼（權證、公司債、可轉債、TDR 次要代碼如 910322）。
  - 此規則會連帶排除 4 碼 DR（存託憑證，如 9103、9105、9110、9136）與少數
    ETF 的 4 碼代號（如 0050、0056）——DR 因無對應產業別指數，本來就會在
    fetch_industry_map.py 標為 unmapped；ETF 依代號規則本來就不會通過此過濾。

用法：
  python fetch_stocks.py --start 2025-05-01 --end 2026-07-18      # 回補區間
  python fetch_stocks.py --update                                  # 只抓今天
  python fetch_stocks.py --start ... --end ... --time-budget-sec 480
      # 加時間預算：跑滿預算就安全中止（raw 快取已落盤，之後重跑同樣參數會
      # 自動跳過已抓日期、從中斷處續傳——冪等設計，不需要額外 checkpoint 檔）
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
RAW_STOCKS_DIR = DATA_DIR / "raw_stocks"
TWSE_RAW_DIR = RAW_STOCKS_DIR / "twse"
TPEX_RAW_DIR = RAW_STOCKS_DIR / "tpex"
TWSE_NON_TRADING_DAYS_PATH = RAW_STOCKS_DIR / "twse_non_trading_days.json"
TPEX_NON_TRADING_DAYS_PATH = RAW_STOCKS_DIR / "tpex_non_trading_days.json"
STOCK_CSV_PATH = DATA_DIR / "stock_prices.csv"

TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={roc}&id=&response=json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

TWSE_TABLE_TITLE_MARKER = "每日收盤行情"
TPEX_REQUIRED_FIELDS = ("代號", "名稱", "收盤")

TWSE_REQUEST_INTERVAL_SEC = 3.5  # 證交所要求 >= 3.5 秒
TPEX_REQUEST_INTERVAL_SEC = 0.7  # 櫃買中心要求 >= 600ms，留餘裕
RETRY_WAIT_SEC = 10

ROLLING_TRADING_DAYS = 300  # RRG 需 120 + 平滑 + 動能約 130，取 300 留裕度

# 普通股代號：4 碼純數字、首碼 1-9（排除 00 開頭 ETF、5/6 碼權證公司債等）
ORDINARY_SHARE_RE = re.compile(r"^[1-9][0-9]{3}$")


class TpexSSLAdapter(HTTPAdapter):
    """放寬 tpex.org.tw 憑證鏈結的 X509_STRICT 檢查（見檔頭註解說明）。

    僅關閉這一項嚴格性旗標，憑證本身仍會正常驗證，不是停用 SSL 驗證。
    """

    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


_tpex_session = requests.Session()
_tpex_session.headers.update(HEADERS)
_tpex_session.mount("https://www.tpex.org.tw/", TpexSSLAdapter())


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_json_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return set()
    if isinstance(data, list):
        return set(data)
    return set()


def save_json_set(path: Path, days: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(days), f, ensure_ascii=False, indent=2)


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def daterange(start: date, end: date):
    d = start
    one_day = timedelta(days=1)
    while d <= end:
        yield d
        d += one_day


def roc_date(d: date) -> str:
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def parse_number(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if s == "" or s == "--":
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# TWSE
# ---------------------------------------------------------------------------


def fetch_twse_day(d: date) -> dict | None:
    ymd = d.strftime("%Y%m%d")
    url = TWSE_URL.format(ymd=ymd)
    last_err = None
    for attempt in (1, 2):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  [twse] attempt {attempt} failed for {ymd}: {e}")
            if attempt == 1:
                time.sleep(RETRY_WAIT_SEC)
    log(f"  [twse] giving up on {ymd} after 2 attempts ({last_err})")
    return None


def find_twse_stock_table(data: dict) -> dict | None:
    for table in data.get("tables") or []:
        title = table.get("title") or ""
        if TWSE_TABLE_TITLE_MARKER in title:
            return table
    return None


def process_twse_dates(dates: list[date], deadline: float | None) -> bool:
    """回傳是否因時間預算提早中止。"""
    TWSE_RAW_DIR.mkdir(parents=True, exist_ok=True)
    non_trading_days = load_json_set(TWSE_NON_TRADING_DAYS_PATH)

    for d in dates:
        if deadline is not None and time.monotonic() >= deadline:
            log("[twse] 已達時間預算，中止（raw 快取已落盤，重跑同樣參數可續傳）")
            return True

        iso = d.isoformat()
        if d.weekday() >= 5:
            continue

        ymd = d.strftime("%Y%m%d")
        raw_path = TWSE_RAW_DIR / f"{ymd}.json"
        if raw_path.exists():
            continue
        if iso in non_trading_days:
            continue

        log(f"[twse] {iso}: 抓取中...")
        data = fetch_twse_day(d)
        time.sleep(TWSE_REQUEST_INTERVAL_SEC)

        if data is None:
            log(f"[twse] {iso}: 抓取失敗，略過（未記入非交易日，之後可重試）")
            continue

        if data.get("stat") != "OK":
            log(f"[twse] {iso}: stat={data.get('stat')!r}，視為非交易日")
            non_trading_days.add(iso)
            save_json_set(TWSE_NON_TRADING_DAYS_PATH, non_trading_days)
            continue

        table = find_twse_stock_table(data)
        if table is None or not (table.get("data") or []):
            log(f"[twse] {iso}: 找不到個股收盤表格或無資料，視為非交易日")
            non_trading_days.add(iso)
            save_json_set(TWSE_NON_TRADING_DAYS_PATH, non_trading_days)
            continue

        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        log(f"[twse] {iso}: 已存 {raw_path.name}（{len(table.get('data') or [])} 列）")

    return False


# ---------------------------------------------------------------------------
# TPEx
# ---------------------------------------------------------------------------


def fetch_tpex_day(d: date) -> dict | None:
    url = TPEX_URL.format(roc=roc_date(d))
    last_err = None
    for attempt in (1, 2):
        try:
            resp = _tpex_session.get(url, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  [tpex] attempt {attempt} failed for {d.isoformat()}: {e}")
            if attempt == 1:
                time.sleep(RETRY_WAIT_SEC)
    log(f"  [tpex] giving up on {d.isoformat()} after 2 attempts ({last_err})")
    return None


def find_tpex_stock_tables(data: dict) -> list[dict]:
    tables = []
    for table in data.get("tables") or []:
        fields = table.get("fields") or []
        if all(f in fields for f in TPEX_REQUIRED_FIELDS):
            tables.append(table)
    return tables


def process_tpex_dates(dates: list[date], deadline: float | None) -> bool:
    TPEX_RAW_DIR.mkdir(parents=True, exist_ok=True)
    non_trading_days = load_json_set(TPEX_NON_TRADING_DAYS_PATH)

    for d in dates:
        if deadline is not None and time.monotonic() >= deadline:
            log("[tpex] 已達時間預算，中止（raw 快取已落盤，重跑同樣參數可續傳）")
            return True

        iso = d.isoformat()
        if d.weekday() >= 5:
            continue

        ymd = d.strftime("%Y%m%d")
        raw_path = TPEX_RAW_DIR / f"{ymd}.json"
        if raw_path.exists():
            continue
        if iso in non_trading_days:
            continue

        log(f"[tpex] {iso}: 抓取中...")
        data = fetch_tpex_day(d)
        time.sleep(TPEX_REQUEST_INTERVAL_SEC)

        if data is None:
            log(f"[tpex] {iso}: 抓取失敗，略過（未記入非交易日，之後可重試）")
            continue

        if data.get("stat") != "ok":
            log(f"[tpex] {iso}: stat={data.get('stat')!r}，異常，略過（未記入非交易日）")
            continue

        tables = find_tpex_stock_tables(data)
        total_rows = sum(len(t.get("data") or []) for t in tables)
        if not tables or total_rows == 0:
            log(f"[tpex] {iso}: 找不到個股收盤表格或無資料，視為非交易日")
            non_trading_days.add(iso)
            save_json_set(TPEX_NON_TRADING_DAYS_PATH, non_trading_days)
            continue

        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        log(f"[tpex] {iso}: 已存 {raw_path.name}（{total_rows} 列）")

    return False


# ---------------------------------------------------------------------------
# 彙整
# ---------------------------------------------------------------------------


def aggregate_twse_rows() -> list[dict]:
    rows = []
    for raw_path in sorted(TWSE_RAW_DIR.glob("*.json")):
        ymd = raw_path.stem
        try:
            d = datetime.strptime(ymd, "%Y%m%d").date()
        except ValueError:
            continue
        iso = d.isoformat()
        with open(raw_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        table = find_twse_stock_table(data)
        if table is None:
            continue
        fields = table.get("fields") or []
        try:
            i_code = fields.index("證券代號")
            i_name = fields.index("證券名稱")
            i_close = fields.index("收盤價")
            i_amount = fields.index("成交金額")
        except ValueError:
            continue
        for row in table.get("data") or []:
            try:
                code = str(row[i_code]).strip()
            except IndexError:
                continue
            if not ORDINARY_SHARE_RE.match(code):
                continue
            close = parse_number(row[i_close]) if i_close < len(row) else None
            if close is None:
                continue
            amount = parse_number(row[i_amount]) if i_amount < len(row) else None
            name = str(row[i_name]).strip() if i_name < len(row) else ""
            rows.append(
                {
                    "date": iso,
                    "market": "twse",
                    "code": code,
                    "name": name,
                    "close": close,
                    "amount": amount if amount is not None else 0.0,
                }
            )
    return rows


def aggregate_tpex_rows() -> list[dict]:
    rows = []
    for raw_path in sorted(TPEX_RAW_DIR.glob("*.json")):
        ymd = raw_path.stem
        try:
            d = datetime.strptime(ymd, "%Y%m%d").date()
        except ValueError:
            continue
        iso = d.isoformat()
        with open(raw_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tables = find_tpex_stock_tables(data)
        for table in tables:
            fields = table.get("fields") or []
            try:
                i_code = fields.index("代號")
                i_name = fields.index("名稱")
                i_close = fields.index("收盤")
                i_amount = fields.index("成交金額(元)")
            except ValueError:
                continue
            for row in table.get("data") or []:
                try:
                    code = str(row[i_code]).strip()
                except IndexError:
                    continue
                if not ORDINARY_SHARE_RE.match(code):
                    continue
                close = parse_number(row[i_close]) if i_close < len(row) else None
                if close is None:
                    continue
                amount = parse_number(row[i_amount]) if i_amount < len(row) else None
                name = str(row[i_name]).strip() if i_name < len(row) else ""
                rows.append(
                    {
                        "date": iso,
                        "market": "tpex",
                        "code": code,
                        "name": name,
                        "close": close,
                        "amount": amount if amount is not None else 0.0,
                    }
                )
    return rows


def aggregate_csv() -> pd.DataFrame:
    rows = aggregate_twse_rows() + aggregate_tpex_rows()
    df = pd.DataFrame(rows, columns=["date", "market", "code", "name", "close", "amount"])
    if df.empty:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(STOCK_CSV_PATH, index=False, encoding="utf-8")
        log(f"已寫入 {STOCK_CSV_PATH}（0 列 — 尚無 raw 資料）")
        return df

    # 滾動窗：只保留最近 ROLLING_TRADING_DAYS 個交易日（以兩市場聯集的日期為準）
    all_dates = sorted(df["date"].unique())
    keep_dates = set(all_dates[-ROLLING_TRADING_DAYS:])
    df = df[df["date"].isin(keep_dates)].copy()

    df = df.sort_values(["date", "market", "code"]).reset_index(drop=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(STOCK_CSV_PATH, index=False, encoding="utf-8")

    n_dates = df["date"].nunique()
    n_codes = df.groupby("market")["code"].nunique().to_dict()
    log(
        f"已寫入 {STOCK_CSV_PATH}（{len(df)} 列，交易日 {n_dates} 天，"
        f"個股數 twse={n_codes.get('twse', 0)} tpex={n_codes.get('tpex', 0)}）"
    )
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取上市＋上櫃全市場個股日收盤")
    parser.add_argument("--start", type=str, help="回補起始日 YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="回補結束日 YYYY-MM-DD")
    parser.add_argument("--update", action="store_true", help="只抓今天")
    parser.add_argument(
        "--time-budget-sec",
        type=float,
        default=None,
        help="超過此秒數就安全中止抓取（raw 快取冪等，重跑同樣參數可續傳）",
    )
    parser.add_argument(
        "--skip-aggregate",
        action="store_true",
        help="只抓 raw、不重新彙整 stock_prices.csv（分段回補時可加快單次執行）",
    )
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

    start_time = time.monotonic()
    deadline = start_time + args.time_budget_sec if args.time_budget_sec else None

    # 先跑節流較嚴格的 TWSE（避免時間預算用完時 TPEx 完全沒機會跑），
    # 再跑 TPEx；兩邊各自檢查 deadline。
    twse_truncated = process_twse_dates(dates, deadline)
    tpex_truncated = process_tpex_dates(dates, deadline)

    if not args.skip_aggregate:
        aggregate_csv()

    if twse_truncated or tpex_truncated:
        log("本次執行因時間預算提早中止；用同樣的 --start/--end 重跑即可從中斷處續傳。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
