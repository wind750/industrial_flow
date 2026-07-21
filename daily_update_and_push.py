#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_update_and_push.py — 產業輪動雷達每日盤後自動更新（本機排程主力）

一條龍：抓當日類股指數（上市/上櫃）＋個股日收盤 → 算雙強排行 → git commit＋push。
push 後 Streamlit Cloud 偵測新 commit 自動重新部署，股友端即更新。

設計為「本機 Windows 排程 16:45 執行」的主力，與 GitHub Actions cron（備援）並存；
git 冪等：若當日資料已被雲端備援 push 過，這裡 fetch 到同資料、無 diff 即不 commit。

非交易日：各 fetch --update 抓不到資料會自行略過，最終無 diff → 不 commit → 正常結束。
任一 fetch 失敗不中斷整體（記 log 續跑），只要有任何檔案更新就 commit push。
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PY = sys.executable
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / f"daily_push_{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_step(desc: str, args: list[str]) -> bool:
    """跑一個子步驟，回傳成功與否；失敗不拋例外（記 log 續跑）。"""
    log(f"START: {desc}")
    try:
        r = subprocess.run([PY, *args], cwd=SCRIPT_DIR, capture_output=True,
                           text=True, encoding="utf-8", timeout=1800)
        tail = "\n".join((r.stdout or "").strip().splitlines()[-2:])
        log(f"  {desc} exit={r.returncode} | {tail}")
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        log(f"  {desc} 例外: {e}")
        return False


def git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=SCRIPT_DIR, capture_output=True,
                          text=True, encoding="utf-8", timeout=300)


def main() -> int:
    log("=== 每日盤後更新開始 ===")

    # 1. 抓資料（任一失敗不中斷）
    run_step("上市類股", ["fetch_twse_sector.py", "--update"])
    run_step("上櫃類股", ["fetch_tpex_sector.py", "--update"])
    run_step("個股日收盤", ["fetch_stocks.py", "--update"])
    # 2. 算雙強排行（sector 的 rrg_data.js 是 Streamlit 動態算、不需在此產）
    run_step("雙強排行", ["compute_stocks.py"])

    # 3. push——「以遠端為基底重掛變更」策略，不用 rebase：
    #    fetch 後 git reset --soft origin/main 把 HEAD 對齊遠端、本次抓到的
    #    變更全數保留在暫存區，再 commit+push。不管雲端備援（Actions cron）
    #    有沒有先推過，都不會發生 rebase 衝突或 non-fast-forward 拒絕；
    #    若遠端已含相同資料，暫存 diff 為空自然跳過。
    #    （2026-07-21 事故教訓：pull --rebase 放在檔案已修改之後會被
    #    「unstaged changes」擋下，導致 push 失敗、資料卡本機。）
    log("START: git 同步與推送")
    fetch = git(["fetch", "origin"])
    if fetch.returncode != 0:
        log(f"  fetch 失敗: {fetch.stderr.strip()[-120:]}")

    git(["add", "data/sector_indices.csv", "data/tpex_indices.csv",
         "data/stock_prices.csv", "data/stock_rankings.json",
         "data/raw", "data/raw_tpex", "data/raw_stocks"])
    reset = git(["reset", "--soft", "origin/main"])
    if reset.returncode != 0:
        log(f"  reset --soft 失敗: {reset.stderr.strip()[-120:]}")
        return 1
    # 只看「已暫存」的變更——工作區的未追蹤檔（logs/ 等）不算數，
    # 否則非交易日也會誤判有變更、誤報「已推送」。
    staged = git(["diff", "--cached", "--quiet"])
    if staged.returncode == 0:
        log("  無資料變更（可能非交易日或雲端已更新），略過 commit。")
        log("=== 結束（無變更）===")
        return 0

    today = f"{datetime.now():%Y-%m-%d}"
    commit = git(["commit", "-m", f"chore: 每日資料自動更新 {today}（本機排程）"])
    log(f"  commit: {commit.stdout.strip().splitlines()[0] if commit.stdout.strip() else commit.stderr.strip()[:120]}")
    push = git(["push", "origin", "main"])
    ok = push.returncode == 0
    log(f"  push: {'成功' if ok else '失敗 ' + push.stderr.strip()[-160:]}")
    log(f"=== 結束（{'已推送' if ok else 'push 失敗'}）===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
