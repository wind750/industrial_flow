#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_industry_map.py

抓取上市／上櫃公司基本資料（含產業別代碼），對映到本專案 RRG 面板已有的
類股／產業指數名稱，輸出 data/industry_map.csv：

  market,code,name,industry_code,industry_name,sector_index_name

資料源（2026-07-18 實測確認）：
  上市：https://openapi.twse.com.tw/v1/opendata/t187ap03_L
        欄位「公司代號」「公司簡稱」「產業別」（產業別為兩碼代碼字串，如 "24"）。
        實測 1090 筆。
  上櫃：https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O
        欄位「SecuritiesCompanyCode」「CompanyAbbreviation」「SecuritiesIndustryCode」
        （同樣兩碼代碼字串）。實測 891 筆。

產業別代碼 → 中文名稱對照表（INDUSTRY_CODE_NAMES）沿用「上市上櫃產業分類代碼」
公開慣例（與本機另一子專案 艾略特波浪架構/資金流向/tide_sector_flow.py 的
INDUSTRY_NAMES 對照表一致，唯讀參考、未修改該檔案），代碼 "91" 為本次新增
（存託憑證 DR，該檔案未收錄，此處補上以利判斷 unmapped 原因）。

代碼 → sector_index_name 的對映分市場各自建立：
  - TWSE_CODE_TO_SECTOR：對映到 data/sector_indices.csv 現有的「XX類指數」
    名稱。電子類 8 個細分類（半導體/電腦及週邊設備/光電/通信網路/電子零組件/
    電子通路/資訊服務/其他電子）全部對到，複合類（水泥窯製/塑膠化工/機電/
    化學生技醫療/電子工業）不接個股——因為它們本身是其他細分類的加總，
    對映會造成個股被複合類與細分類雙重認列。
  - TPEX_CODE_TO_SECTOR：對映到 data/tpex_indices.csv 現有的產業類股名稱
    （上櫃指數 22 類、無「類指數」尾綴，如「半導體業」「光電業」）。上櫃
    「電子工業」與上市「電子工業類指數」同樣是電子類全體的複合／彙總指數，
    不接個股（沒有任何 industry_code 對映到它，個股仍分流到各電子細分業）。

對不到的個股（代碼查無 sector_index_name）保留在檔案內，sector_index_name
欄位標記為 "unmapped"，industry_name 欄位仍記錄查到的產業別名稱供人工核對，
查無代碼本身對照表的則 industry_name 也標 "unknown"。

用法：
  python fetch_industry_map.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
INDUSTRY_MAP_CSV_PATH = DATA_DIR / "industry_map.csv"

TWSE_OPENAPI_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_OPENAPI_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# 產業別代碼 → 中文產業名稱（人工核對用，不代表一定有對映的類股指數）
INDUSTRY_CODE_NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
    "15": "航運業", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨",
    "19": "綜合企業", "20": "其他業", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業",
    "26": "光電業", "27": "通信網路業", "28": "電子零組件業",
    "29": "電子通路業", "30": "資訊服務業", "31": "其他電子業",
    "32": "文化創意業", "33": "農業科技業", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
    "91": "存託憑證(DR)",
}

# 人工核對表：TWSE 產業別代碼 → data/sector_indices.csv 的類股指數名稱。
# 電子類 8 個細分類全部對到；5 個複合類（水泥窯製/塑膠化工/機電/化學生技醫療/
# 電子工業）刻意不出現在這裡（不接個股）。
# 19 綜合企業 / 32 文化創意業 / 33 農業科技業 / 34 電子商務 / 91 DR：
# TWSE 目前無對應的獨立類股指數 → unmapped。
TWSE_CODE_TO_SECTOR = {
    "01": "水泥類指數",
    "02": "食品類指數",
    "03": "塑膠類指數",
    "04": "紡織纖維類指數",
    "05": "電機機械類指數",
    "06": "電器電纜類指數",
    "08": "玻璃陶瓷類指數",
    "09": "造紙類指數",
    "10": "鋼鐵類指數",
    "11": "橡膠類指數",
    "12": "汽車類指數",
    "14": "建材營造類指數",
    "15": "航運類指數",
    "16": "觀光餐旅類指數",
    "17": "金融保險類指數",
    "18": "貿易百貨類指數",
    "20": "其他類指數",
    "21": "化學類指數",
    "22": "生技醫療類指數",
    "23": "油電燃氣類指數",
    "24": "半導體類指數",
    "25": "電腦及週邊設備類指數",
    "26": "光電類指數",
    "27": "通信網路類指數",
    "28": "電子零組件類指數",
    "29": "電子通路類指數",
    "30": "資訊服務類指數",
    "31": "其他電子類指數",
    "35": "綠能環保類指數",
    "36": "數位雲端類指數",
    "37": "運動休閒類指數",
    "38": "居家生活類指數",
}

# 人工核對表：TPEx 產業別代碼 → data/tpex_indices.csv 的類股名稱。
# 「電子工業」是電子類全體的複合指數，同樣不接個股（無代碼對映到它）。
# 02 食品/03 塑膠/06 電器電纜/17 金融保險/23 油電燃氣/33 農業科技/37 運動休閒：
# 上櫃該產業掛牌家數過少，櫃買中心未單獨編製指數 → unmapped。
TPEX_CODE_TO_SECTOR = {
    "04": "紡織纖維",
    "05": "電機機械",
    "10": "鋼鐵工業",
    "14": "建材營造",
    "15": "航運業",
    "16": "觀光餐旅",
    "20": "其他",
    "21": "化學工業",
    "22": "生技醫療",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    "32": "文化創意業",
    "35": "綠能環保",
    "36": "數位雲端",
    "38": "居家生活",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch_json(url: str) -> list[dict]:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_rows() -> list[dict]:
    rows: list[dict] = []

    twse_data = fetch_json(TWSE_OPENAPI_URL)
    for r in twse_data:
        code = (r.get("公司代號") or "").strip()
        name = (r.get("公司簡稱") or "").strip()
        industry_code = (r.get("產業別") or "").strip()
        if not code:
            continue
        industry_name = INDUSTRY_CODE_NAMES.get(industry_code, "unknown")
        sector_index_name = TWSE_CODE_TO_SECTOR.get(industry_code, "unmapped")
        rows.append(
            {
                "market": "twse",
                "code": code,
                "name": name,
                "industry_code": industry_code,
                "industry_name": industry_name,
                "sector_index_name": sector_index_name,
            }
        )

    tpex_data = fetch_json(TPEX_OPENAPI_URL)
    for r in tpex_data:
        code = (r.get("SecuritiesCompanyCode") or "").strip()
        name = (r.get("CompanyAbbreviation") or "").strip()
        industry_code = (r.get("SecuritiesIndustryCode") or "").strip()
        if not code:
            continue
        industry_name = INDUSTRY_CODE_NAMES.get(industry_code, "unknown")
        sector_index_name = TPEX_CODE_TO_SECTOR.get(industry_code, "unmapped")
        rows.append(
            {
                "market": "tpex",
                "code": code,
                "name": name,
                "industry_code": industry_code,
                "industry_name": industry_name,
                "sector_index_name": sector_index_name,
            }
        )

    return rows


def write_csv(rows: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = ["market", "code", "name", "industry_code", "industry_name", "sector_index_name"]
    rows_sorted = sorted(rows, key=lambda r: (r["market"], r["code"]))
    with open(INDUSTRY_MAP_CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_sorted)
    log(f"已寫入 {INDUSTRY_MAP_CSV_PATH}（{len(rows_sorted)} 列）")


def print_coverage_report(rows: list[dict]) -> None:
    for market in ("twse", "tpex"):
        market_rows = [r for r in rows if r["market"] == market]
        total = len(market_rows)
        mapped = [r for r in market_rows if r["sector_index_name"] != "unmapped"]
        unmapped = [r for r in market_rows if r["sector_index_name"] == "unmapped"]
        log(f"--- {market} ---")
        log(f"  總筆數: {total}, 已對映: {len(mapped)}, unmapped: {len(unmapped)}")
        if unmapped:
            reasons: dict[str, int] = {}
            for r in unmapped:
                reasons[r["industry_name"]] = reasons.get(r["industry_name"], 0) + 1
            for reason, cnt in sorted(reasons.items(), key=lambda kv: -kv[1]):
                log(f"    unmapped 原因分佈: {reason} x{cnt}")


def main() -> int:
    rows = build_rows()
    write_csv(rows)
    print_coverage_report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
