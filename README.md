# 產業輪動雷達（RRG）資料管線

台股產業（類股）資金輪動視覺化的資料管線。概念參考 StockCharts 的 Relative
Rotation Graph（RRG）：橫軸為相對強度（RS-Ratio），縱軸為相對強度的動能
（RS-Momentum），用來觀察各產業相對大盤／臺灣50指數的資金輪動位置。

資料源為證交所公開 API（不依賴 XQ 產業模組）：

```
https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={YYYYMMDD}&type=IND&response=json
```

## 檔案

| 檔案 | 用途 |
|---|---|
| `fetch_twse_sector.py` | 抓取證交所「價格指數(臺灣證券交易所)」表格（約 56 檔指數，含加權指數、臺灣50指數與各類指數），存原始 JSON 並彙整成 `data/sector_indices.csv` |
| `compute_rrg.py` | 讀 `data/sector_indices.csv`，對兩個基準（發行量加權股價指數、臺灣50指數）× 三個週期（20/60/120 日）計算 RRG 座標，輸出 `web/rrg_data.js` |
| `update_daily.bat` | 每日排程用：依序執行「抓今天」+「重算」，任一步失敗就回傳非 0 exit code |

`web/rrg.html`（前端頁面）由另一位開發者維護，不屬於本管線範圍。

## 資料目錄

- `data/raw/{YYYYMMDD}.json` — 每日原始回應（僅在 `stat == "OK"` 且有資料時存檔）。已存在的日期會直接跳過，重跑不會重抓（冪等）。
- `data/non_trading_days.json` — 已知非交易日（API 回傳 `stat != "OK"`）清單，之後回補會直接跳過、不重打。
- `data/sector_indices.csv` — 彙整後的長表格，欄位 `date`（ISO 格式）,`name`,`close`（float）。每次抓取後會用 `data/raw/` 下所有檔案重新彙整整份 CSV。

## 用法

### 回補歷史區間

```
python fetch_twse_sector.py --start 2026-07-10 --end 2026-07-16
```

- 自動跳過週六日。
- 每次 API 請求間隔至少 3.5 秒（證交所會 ban 過快的請求）。
- 單日失敗會等 10 秒重試 1 次，仍失敗則記錄到 log 並繼續下一天（不會寫入
  `non_trading_days.json`，之後可再次嘗試回補）。

### 每日更新（只抓今天）

```
python fetch_twse_sector.py --update
```

### 計算 RRG 座標

```
python compute_rrg.py
```

預設輸出到 `web/rrg_data.js`（`window.RRG_DATA = {...};`）。可用 `--out` 覆寫輸出位置，例如做 smoke test 時避免覆蓋正式檔案：

```
python compute_rrg.py --out data\_smoke_rrg_data.js
```

### 每日排程批次檔

```
update_daily.bat
```

依序執行 `fetch_twse_sector.py --update` 與 `compute_rrg.py`；任一步失敗會印出錯誤並以 exit code 1 結束。

## RRG 座標公式

採公開近似算法（非 JdK 原版公式，JdK 原版未公開）：

```
rs           = 100 * sector_close / benchmark_close
rs_ratio     = 100 + (rs  - rolling_mean(rs,  W)) / rolling_std(rs,  W)
mom          = rs_ratio 的 5 日變化率 (pct_change(5) * 100)
rs_momentum  = 100 + (mom - rolling_mean(mom, W)) / rolling_std(mom, W)
```

`W` 為週期（20/60/120 日）。歷史不足、算不出的日期一律填 `null`，不會用估值或 0 填補。

## 依賴

`requests`、`pandas`（皆為本機環境既有套件，未額外安裝）。

## Streamlit 部署

雲端版 `streamlit_app.py` 讀 `data/sector_indices.csv`，重用 `compute_rrg.py`
的計算核心，提供互動式 RRG 動畫（基準／週期／回放範圍／尾巴長度／族群篩選）。
`web/rrg.html` 為另一位開發者維護的本機版頁面，與雲端版無關。

1. 建 GitHub repo（例如 `industry-rotation-radar`），把本目錄整個推上去：
   ```
   git init
   git add .
   git commit -m "init"
   git remote add origin https://github.com/wind750/<repo-name>.git
   git push -u origin main
   ```
2. 到 [share.streamlit.io](https://share.streamlit.io) 連結該 repo，Main file
   path 填 `streamlit_app.py`，部署即可。
3. `.github/workflows/update_data.yml` 已設定週一到週五台北時間 16:30 自動抓
   當日證交所類股指數並 commit 回 repo；push 後 Streamlit Cloud 會偵測到
   `data/` 變更並在下次讀取時反映（`st.cache_data` TTL 為 1 小時）。也可在
   GitHub Actions 頁面手動 `Run workflow` 立即回補。
4. Repo 的 Settings → Actions → General 需確認 Workflow permissions 允許
   「Read and write permissions」，否則 Actions 無法 push 更新（本 workflow
   已在 YAML 內宣告 `permissions: contents: write`，但部分機構帳號的預設
   repo 設定仍需手動開啟一次）。
