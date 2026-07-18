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
| `fetch_global.py` | 抓取「全球資產」「市場輪動」兩面板用的海外 ETF／指數歷史日線收盤，存成 `data/global_prices.csv`（詳見下方〈全球資產／市場輪動面板〉） |
| `compute_global.py` | 讀 `data/global_prices.csv`，重用 `compute_rrg.py` 的計算核心，輸出 `web/rrg_data_global.js` |
| `update_daily.bat` | 每日排程用：依序執行「抓今天」+「重算」，任一步失敗就回傳非 0 exit code |

`web/rrg.html`（前端頁面）由另一位開發者維護，不屬於本管線範圍；但已支援讀取
`rrg_data_global.js`（若存在）並在同一頁面切換「台股類股／全球資產／市場輪動」
三個面板。

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
rs_ratio_raw = 100 + (rs  - rolling_mean(rs,  W)) / rolling_std(rs,  W)
rs_ratio     = trailing_wma(rs_ratio_raw, SMOOTH_WINDOW)
mom          = rs_ratio 的 5 日變化率 (pct_change(5) * 100)   # 吃平滑後的 rs_ratio
mom_raw      = 100 + (mom - rolling_mean(mom, W)) / rolling_std(mom, W)
rs_momentum  = trailing_wma(mom_raw, SMOOTH_WINDOW)
```

`W` 為週期（20/60/120 日）。歷史不足、算不出的日期一律填 `null`，不會用估值或 0 填補。

### 平滑層（trailing WMA）

`rs_ratio` 與 `rs_momentum` 各再過一道 **trailing 線性加權移動平均**
（`compute_rrg.py` 的 `trailing_wma()`），目的是把 zscore 序列的鋸齒感磨掉，
讓軌跡讀起來更像連續走勢，而不是為了改變訊號本質。

- 視窗常數 `SMOOTH_WINDOW = 4`（`compute_rrg.py` 模組層級），權重
  `1, 2, ..., SMOOTH_WINDOW`，最新的觀測值權重最高。
- `min_periods=1`：序列開頭資料不足一整個視窗時，改用當下可取得的較短
  視窗（權重相應縮短為 `1..n`），仍然只用「當下與更早」的資料。
- 鏈路順序是 `rs_ratio`（平滑後）→ `mom`（5 日變化率，吃平滑後的
  `rs_ratio`）→ `rs_momentum`（平滑後）。也就是說 momentum 的平滑效果會
  疊加在 ratio 的平滑效果之上。
- **只用過去資料，無前視（lookahead）**：pandas `rolling()` 預設右對齊
  （trailing），任一時點 `t` 的平滑值只依賴 `t` 及更早的觀測，不會用到
  未來資料。用「歷史前綴重算 vs. 全量重算，同一天座標應完全一致」可驗證
  這點。
- **代價是相位延遲（lag）**：加權移動平均會讓轉折點的反應延後，
  `SMOOTH_WINDOW=4` 大約對應 2 個交易日的相位延遲（權重集中在最近幾筆，
  但仍非零延遲）——這是「軌跡變優雅」與「反應變即時」之間刻意的取捨。
- 要關閉平滑、回到未平滑的原始 zscore 序列：把 `compute_rrg.py` 的
  `SMOOTH_WINDOW` 改成 `0` 或 `1`（`trailing_wma()` 會直接原樣回傳輸入）。

## 全球資產／市場輪動面板

除了台股類股，`web/rrg.html` 另外支援兩個海外面板：「全球資產」（v2 起雙
基準、16／15 個資產類別 ETF＋期貨／指數，視基準而定）與「市場輪動」（12
個國家／區域股市 ETF，單一基準）。三個面板共用同一套播放引擎（插值、平滑、
Catmull-Rom 尾巴、剛轉強偵測），只有資料來源、基準與面板成員不同。

### 品項表

「市場輪動」面板單一基準（`fetch_global.py`/`compute_global.py` 代碼
`acwi.us`）：**全球股票 ACWI**。

「全球資產」面板（v2）為**雙基準**：**全球股票 ACWI** ＋ **美元指數
DXY**（yfinance 代碼 `DX-Y.NYB`，stooq 無此指數，僅 yfinance 單一來源）。
兩個基準的面板成員集合不完全相同：

「全球資產」面板 · 全球股票 ACWI 基準（16 項，含「美元 UUP」）：

| 代碼 | 面板顯示名 |
|---|---|
| spy.us | 美股 SPY |
| qqq.us | 美股科技 QQQ |
| tlt.us | 美債20年 TLT |
| ief.us | 美債7-10年 IEF |
| lqd.us | 投資級債 LQD |
| hyg.us | 高收益債 HYG |
| gld.us | 黃金 GLD |
| slv.us | 白銀 SLV |
| CL=F | 原油 WTI |
| HG=F | 銅 |
| dbc.us | 商品 DBC |
| vnq.us | 房地產 VNQ |
| btcusd | 比特幣 BTC |
| fxe.us | 歐元 FXE |
| fxy.us | 日圓 FXY |
| uup.us | 美元 UUP |

「全球資產」面板 · 美元指數 DXY 基準（15 項，排除「美元 UUP」——自己除自己
無意義，其餘 15 項與上表共用同一份底層資料）：上表扣掉 `uup.us` 那一列。

預設隱藏（初見畫面較不擁擠，可在族群清單勾回來，兩個基準皆適用同一份名單）：
白銀 SLV、投資級債 LQD、美債7-10年 IEF、歐元 FXE。

**切換基準時族群清單會跟著重建**：可見成員＝目前基準底下實際存在的
`series[基準][週期]` keys；勾選狀態依名稱盡量保留（兩基準都有、原本勾著的
維持勾著；兩基準都有、原本沒勾的維持不勾；只有新基準才有的成員依上述
`default_hidden` 名單決定初始勾選狀態）。切換基準不會重置播放頭（維持既有
語義，與切換週期／回放範圍相同）。

**兩個特殊品項的注意事項：**

- `CL=F`（原油 WTI）、`HG=F`（銅）皆為**期貨連續合約**（continuous
  contract），由資料源（yfinance／Yahoo Finance）自動接續近月合約構成，
  轉倉（roll）時可能出現與現貨脫節的跳動，不是真實的單一到期日合約價格；
  長期趨勢仍具參考性，但短期跳動需留意轉倉雜訊。
- `DX-Y.NYB`（美元指數 DXY）是**指數**，不是可直接投資的商品／ETF（沒有
  對應的可交易標的可以 1:1 追蹤 DXY 本身）；本管線把它當「美元強弱」的
  參照分母使用，不代表存在對應的可投資部位。
- **美元基準視角的解讀**：把 DXY 當分母時，RRG 呈現的是「各資產相對美元
  強弱」的輪動，可視為一種**風險偏好儀表（risk appetite gauge）**——美元
  走強（DXY 領先）時通常伴隨風險資產（股票、原油、新興市場貨幣）落後或
  弱化象限、避險資產（美債、日圓）相對抗跌；美元走弱則反過來。這與
  ACWI 基準呈現的「相對全球股票的資金輪動」是互補而非取代的兩種視角，
  不建議只看其中一個基準下結論。
- 商品期貨（原油、銅）**僅有 yfinance 單一來源**，stooq 對這兩個代碼無
  資料，因此 `fetch_global.py` 對它們不會嘗試 stooq，直接打 yfinance；
  若 yfinance 當次抓取失敗，這兩項會整批略過（不像其他品項有雙來源互為
  備援），下次排程重跑會再試一次。

「市場輪動」面板（12 項，`spy.us` 與資產面板共用同一份底層資料，僅顯示名不同）：

| 代碼 | 面板顯示名 |
|---|---|
| spy.us | 美國 |
| ewt.us | 台灣 |
| ewj.us | 日本 |
| ewy.us | 南韓 |
| mchi.us | 中國 |
| inda.us | 印度 |
| vnm.us | 越南 |
| ewz.us | 巴西 |
| ewa.us | 澳洲 |
| ewu.us | 英國 |
| ewg.us | 德國 |
| vgk.us | 歐洲 |

### 資料源與備援

主要資料源為 [stooq](https://stooq.com/) 的歷史日線 CSV 端點
（`https://stooq.com/q/d/l/?s={symbol}&i=d`，一次回傳整段歷史）。截至本文件
撰寫時，stooq 對本環境的請求會回傳一個需要執行 JavaScript 才能通過的機器人
驗證頁面（proof-of-work 挑戰），非傳統的登入或付費牆——`fetch_global.py`
偵測到回應不是預期的 CSV 格式時視為主源失敗，**不會**嘗試用程式解那個挑戰，
而是自動切換到備援源 [yfinance](https://github.com/ranaroussi/yfinance)（非
官方套件，透過 Yahoo Finance 公開頁面撈資料）。

**stooq 與 yfinance 都是非官方的免費資料源**，未簽署任何 SLA，欄位定義、
可用性、請求限制都可能未經通知就變動。本管線只用於研究與可視化，不應作為
交易執行或法遵用途的權威報價來源。`fetch_global.py` 對每個品項都會先試
stooq、失敗再試 yfinance，兩者都失敗則記錄該品項失敗並繼續處理下一個，不會
讓整批抓取中斷；`yfinance` 刻意不寫入 `requirements.txt`（避免影響
`streamlit_app.py` 的雲端部署依賴），只在本機或 CI 需要重跑
`fetch_global.py` 時才另外安裝。

**例外：v2 新增的 3 個品項——`CL=F`（原油 WTI）、`HG=F`（銅）、`DX-Y.NYB`
（美元指數 DXY）——stooq 沒有對應代碼，`fetch_global.py` 對它們直接跳過
stooq、只打 yfinance**（`SYMBOLS` 清單中把 `stooq_symbol` 設為 `None` 即代表
這個意思）。這三項因此沒有雙來源備援：yfinance 當次失敗就整批略過該品項，
不像其他品項有 stooq/yfinance 互為備援，下次排程重跑會再試。

### 對齊規則

不同市場的交易日曆彼此不同步（美股假日 ≠ 台股假日 ≠ 加密貨幣 7×24 交易），
`compute_global.py` 對每個面板各自：

1. 取該面板實際用到的品項（基準 + 面板成員）在 `global_prices.csv` 中出現的
   日期範圍聯集，建出 Mon–Fri 工作日曆（不含週六日）。
2. 把每個品項的收盤價 reindex 到這個工作日曆上，各自 forward-fill 最多 5 個
   工作日；超過 5 個工作日仍缺值則維持 `null`，不臆測填補。
3. 全部以 USD 計價，不做匯率轉換。

比特幣（`btcusd`）雖然 7×24 交易，但工作日曆本身只有 Mon–Fri，週末的資料點
自然不會出現在輸出裡（不是被 ffill 蓋掉，而是根本不在日曆範圍內），因此不會
把「週末沒交易」誤判成「資料缺漏」。

### 更新排程

`.github/workflows/update_data.yml` 內第二個排程 `0 22 * * 1-5`（台北時間
隔日 06:00，美股收盤後）會依序執行 `fetch_global.py` → `compute_global.py`
（後者在 CI 內只作為資料品質檢查，其產物 `web/rrg_data_global.js` 不進版控，
只有 `data/global_prices.csv` 會被 commit）。兩個排程（台股 08:30 UTC／全球
22:00 UTC）各自對應獨立的 job，互不干擾——細節見該 workflow 檔案開頭註解。

### 本機用法

```
python fetch_global.py       # 抓（或重抓）全部品項的歷史日線
python compute_global.py     # 計算 RRG 座標，輸出 web/rrg_data_global.js
```

`web/rrg_data_global.js` 不進版控（比照 `web/rrg_data.js` 現行做法），本機
直開 `web/rrg.html` 時若這個檔案不存在，頁面會靜默略過全球面板、只顯示台股
類股，不會出錯。

## 依賴

`requests`、`pandas`（皆為本機環境既有套件，未額外安裝）。全球面板的備援源
需要 `yfinance`（見上方〈資料源與備援〉），刻意不放進 `requirements.txt`。

## Streamlit 部署

雲端版 `streamlit_app.py` 讀 `data/sector_indices.csv` 與（若存在）
`data/global_prices.csv`，重用 `compute_rrg.py`／`compute_global.py` 的計算
核心，提供互動式 RRG 動畫（基準／週期／回放範圍／尾巴長度／族群篩選／面板
切換）。`web/rrg.html` 為另一位開發者維護的本機版頁面；雲端版把
`window.RRG_DATA` 與 `window.RRG_DATASETS_GLOBAL` 動態算好後內嵌進同一份
`rrg.html` 原始碼再用 `st.iframe` 整頁嵌入，兩邊共用同一份前端邏輯。

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
3. `.github/workflows/update_data.yml` 已設定兩個排程：週一到週五台北時間
   16:30 自動抓當日證交所類股指數（`update-twse` job），以及週一到週五台北
   時間隔日 06:00 自動抓全球資產／市場輪動資料（`update-global` job），皆
   commit 回 repo；push 後 Streamlit Cloud 會偵測到 `data/` 變更並在下次
   讀取時反映（`st.cache_data` TTL 為 1 小時）。也可在 GitHub Actions 頁面
   手動 `Run workflow`，用 `target` 輸入選擇只回補台股／只回補全球／兩者
   都跑。
4. Repo 的 Settings → Actions → General 需確認 Workflow permissions 允許
   「Read and write permissions」，否則 Actions 無法 push 更新（本 workflow
   已在 YAML 內宣告 `permissions: contents: write`，但部分機構帳號的預設
   repo 設定仍需手動開啟一次）。
