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
| `fetch_tpex_sector.py` | 抓取櫃買中心（TPEx）產業類股價格指數，存原始 JSON 並彙整成 `data/tpex_indices.csv`（詳見下方〈上櫃類股面板〉） |
| `compute_tpex.py` | 讀 `data/tpex_indices.csv`，以「櫃買指數」為單一基準 × 三個週期（20/60/120 日）計算 RRG 座標，輸出 `web/rrg_data_tpex.js` |
| `fetch_global.py` | 抓取「全球資產」「市場輪動」兩面板用的海外 ETF／指數歷史日線收盤，存成 `data/global_prices.csv`（詳見下方〈全球資產／市場輪動面板〉） |
| `compute_global.py` | 讀 `data/global_prices.csv`，重用 `compute_rrg.py` 的計算核心，輸出 `web/rrg_data_global.js` |
| `fetch_stocks.py` | 抓取上市＋上櫃全市場個股日收盤，存原始 JSON 並彙整成 `data/stock_prices.csv`（滾動最近 300 個交易日；詳見下方〈個股雙強排行〉） |
| `fetch_industry_map.py` | 抓取上市／上櫃公司產業別代碼，對映到本專案既有的類股指數名稱，輸出 `data/industry_map.csv` |
| `compute_stocks.py` | 讀 `stock_prices.csv`＋`industry_map.csv`＋兩份類股指數 CSV，計算「產業內個股雙強」排行，輸出 `web/rrg_data_stocks.js`／`data/stock_rankings.json` |
| `update_daily.bat` | 每日排程用：依序執行「抓今天」+「重算」，任一步失敗就回傳非 0 exit code |

`web/rrg.html`（前端頁面）由另一位開發者維護，不屬於本管線範圍；但已支援讀取
`rrg_data_tpex.js`／`rrg_data_global.js`（若存在）並在同一頁面切換「台股類股／
上櫃類股／全球資產／市場輪動」四個面板。`rrg_data_stocks.js`／
`stock_rankings.json`（個股雙強排行）**目前只有資料層，前端尚未整合**——
`rrg.html`／`streamlit_app.py` 尚不會讀取或顯示這份資料，屬於下一階段工作。

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

## 上櫃類股面板

除了上市台股類股，`web/rrg.html` 另外支援「上櫃類股」面板：資料源為
**櫃買中心（TPEx）**公開的產業類股價格指數（與上市面板同樣走
`compute_rrg.py` 的計算核心，只是資料源與成員清單不同）：

```
https://www.tpex.org.tw/www/zh-tw/afterTrading/indexSummary?date={YYYY/MM/DD}&response=json
```

（實際端點與參數細節見 `fetch_tpex_sector.py`；輸出彙整成
`data/tpex_indices.csv`，欄位與 `data/sector_indices.csv` 相同：`date`,`name`,`close`。）

- **基準**：單一基準「櫃買指數」（不像上市面板有雙基準）。
- **週期**：與其他面板一致，20／60／120 日三組。
- **成員（22 個上櫃產業類股）**：紡織纖維、電機機械、鋼鐵工業、電子工業、
  建材營造、航運業、觀光餐旅、其他、化學工業、生技醫療、半導體業、
  電腦及週邊設備業、光電業、通信網路業、電子零組件業、電子通路業、
  資訊服務業、其他電子業、文化創意業、綠能環保、數位雲端、居家生活
  （不含富櫃50/200、公司治理、ESG、櫃買總指數等主題／市場指數；完整清單見
  `compute_tpex.py` 的 `TPEX_SECTORS`）。

**與上市面板的區別**：上市（`sector_indices.csv`）以傳產／權值股為主的類股
結構為主；上櫃則明顯偏向題材型／成長型產業（文化創意業、數位雲端、綠能
環保、生技醫療等佔比較高），兩個面板適合對照著看——上市面板反映大盤主流
資金，上櫃面板則較能捕捉中小型題材股的輪動節奏。

**每日更新**：與上市面板併入同一個排程 job（`update-twse`，見下方
〈更新排程〉），台北時間 16:30（證交所／櫃買中心盤後資料皆已就緒）依序執行
`fetch_twse_sector.py --update` → `fetch_tpex_sector.py --update`，
`data/tpex_indices.csv` 與 `data/raw_tpex/` 一併 commit。雲端版
`streamlit_app.py` 讀 `data/tpex_indices.csv` 動態計算，不依賴
`web/rrg_data_tpex.js`（該檔比照 `rrg_data.js`／`rrg_data_global.js`，
只是本機/CI 產物，不進版控）；`data/tpex_indices.csv` 不存在時頁面會靜默
略過上櫃面板，只顯示其餘面板，不會出錯。

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
只有 `data/global_prices.csv` 會被 commit）。兩個排程（台股／上櫃 08:30
UTC，併入同一個 `update-twse` job；全球 22:00 UTC，`update-global` job）
各自對應獨立的 job，互不干擾——細節見該 workflow 檔案開頭註解與下方
〈上櫃類股面板〉。

### 本機用法

```
python fetch_global.py       # 抓（或重抓）全部品項的歷史日線
python compute_global.py     # 計算 RRG 座標，輸出 web/rrg_data_global.js
```

`web/rrg_data_global.js` 不進版控（比照 `web/rrg_data.js` 現行做法），本機
直開 `web/rrg.html` 時若這個檔案不存在，頁面會靜默略過全球面板、只顯示台股
類股，不會出錯。

## 個股雙強排行

> **這是觀察工具，不是買賣建議，也未經任何回測驗證。** 排行純粹是「相對強度
> 座標」的機械式排序，不代表個股基本面、未來報酬或任何形式的操作建議；使用
> 前請自行判斷並承擔風險。本功能目前**只有資料層**，`web/rrg.html` 與
> `streamlit_app.py` 都還沒有讀取或顯示這份資料——前端整合是下一階段工作。

在既有四面板 RRG（上市 37 類／上櫃 22 類／全球資產／市場輪動）之外，往下鑽一層：
產業轉強之後，在「該產業內」找出相對自己產業指數更強的個股（以下稱「雙強」：
產業本身相對大盤轉強＋個股相對產業指數轉強）。計算核心與既有 RRG 面板共用
`compute_rrg.py` 的 `compute_rs_ratio_momentum()`（含 trailing WMA 平滑、無前視），
只是把「類股 vs. 大盤」換成「個股 vs. 所屬類股指數」。

### 資料源

- **個股日收盤**（`fetch_stocks.py`）：
  - 上市：`https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={YYYYMMDD}&type=ALLBUT0999&response=json`
  - 上櫃：`https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={民國年/MM/DD}&id=&response=json`
  - 上櫃端點在本機會間歇性拋出 `SSLCertVerificationError`（Windows 對其憑證鏈結的
    嚴格驗證問題），已比照另一子專案 `艾略特波浪架構/資金流向/tide_sector_flow.py`
    的解法，用自訂 `HTTPAdapter` 關閉 `ssl.VERIFY_X509_STRICT` 旗標處理（僅放寬
    這一項檢查，非停用 SSL 驗證）。
  - 節流：上市 ≥3.5 秒／請求、上櫃 ≥0.7 秒／請求（皆高於官方要求下限，避免被
    封鎖）。
- **產業別對照**（`fetch_industry_map.py`）：
  - 上市：`https://openapi.twse.com.tw/v1/opendata/t187ap03_L`
  - 上櫃：`https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O`
  - 兩者皆回傳兩碼「產業別代碼」，程式內用人工核對表對映到本專案既有的類股
    指數名稱（`fetch_industry_map.py` 檔案開頭有完整程式化對照表＋人工核對
    註解），只在週一排程更新（公司產業別變動很慢，不需每日打 API）。

### 滾動 300 個交易日窗

`stock_prices.csv` 只保留最近 300 個交易日（RRG 需 120 日窗＋動能 lookback＋
trailing WMA 平滑，約需 130 日才穩定，300 留裕度）。抓取採 raw JSON 冪等快取
（`data/raw_stocks/twse/`、`data/raw_stocks/tpex/`，已存在的日期直接跳過），
彙整時才從全部 raw 檔案中裁到最近 300 天，重跑 `fetch_stocks.py` 不會重複打 API。

**`data/raw_stocks/` 刻意不進版控**（與 `data/raw/`／`data/raw_tpex/` 的既有
做法不同，見 `.gitignore` 註解）：TPEx `dailyQuotes` 端點會把整個上櫃市場
（含近萬筆公司債／可轉債／權證列，不只是我們要的個股）一起回傳，單日原始檔
約 1.7-2MB，300 天回補下來 `data/raw_stocks/tpex/` 高達 580MB+ ——相較整個
repo 的 `.git` 目前僅約 9MB，若進版控會讓 repo 體積長期失控。彙整後真正需要
的 `data/stock_prices.csv`（約 26MB）已進版控；daily 排程只抓「今天」一天，
不進版控 raw 快取不影響每日更新，只在「需要從零重新回補整段歷史」這種罕見
情境才需要重新打 API（約 20-25 分鐘，見下方〈每日更新〉）。

### 普通股過濾規則

只保留代號「4 碼純數字、首碼 1-9」（正則 `^[1-9][0-9]{3}$`）：排除 00 開頭的
ETF／受益證券（如 0050、00679B），也排除 5/6 碼的權證／公司債／可轉債／TDR
次要代碼。此規則會連帶排除 4 碼存託憑證（DR，如 2330 台積電-DR 系列的 9103、
9105 等）——這批個股因無對應產業別指數，本來就會在 `industry_map.csv` 中被
標為 `unmapped`。

### 產業別對映與複合類排除

`fetch_industry_map.py` 把公司產業別代碼對映到本專案面板既有的類股指數名稱。
上市電子業 8 個細分類（半導體／電腦及週邊設備／光電／通信網路／電子零組件／
電子通路／資訊服務／其他電子）全部個別對映；但上市的 5 個**複合類**指數
（水泥窯製／塑膠化工／機電／化學生技醫療／電子工業類指數）**不接個股**——
因為它們本身是其他細分類的加總，若也對映個股會造成同一檔股票在複合類與細分
類雙重認列。上櫃「電子工業」同理視為電子類全體的複合／彙總指數，不接個股。
少數產業（如 TWSE 綜合企業／文化創意業／農業科技業／電子商務，TPEx 食品／
塑膠／電器電纜／金融保險／油電燃氣／農業科技／運動休閒）因掛牌家數過少，
證交所／櫃買中心未單獨編製對應類股指數，個股在 `industry_map.csv` 中標記
`sector_index_name = "unmapped"` 並保留（不刪除），`industry_name` 欄位仍記錄
查到的產業別供人工核對；2026-07-18 實測覆蓋率：上市 1090 檔中 1080 檔對映
成功（僅 10 檔 DR unmapped），上櫃 891 檔中 851 檔對映成功（40 檔 unmapped）。

### 流動性過濾與雙強定義

- **流動性門檻**：個股 20 日均成交金額 ≥ 3,000 萬元（`compute_stocks.py` 的
  `LIQUIDITY_FLOOR_AMOUNT` 常數，可調）才入榜，避免冷門股的雜訊排名。
- **雙強定義**：個股最新 RS-Ratio（x 座標）≥ 100，即相對自己的產業指數轉強。
  不看 y（RS-Momentum）——排行只看「現在強不強」，不篩「加速中/減速中」。
- 每個產業、每個週期（20／60／120 日）各自依 x 降冪取前 10 檔，不足 10 檔就
  有幾檔列幾檔。

### 輸出格式

`web/rrg_data_stocks.js`（`window.RRG_STOCK_RANKINGS = {...};`，未進版控，本機/CI
產物）與 `data/stock_rankings.json`（同內容純 JSON，進版控，供 Streamlit／其他
程式讀取）：

```json
{
  "as_of": "2026-07-17",
  "liquidity_floor": 30000000,
  "panels": {
    "twse": {
      "半導體類指數": {
        "20": [{"code": "2330", "name": "台積電", "x": 101.2, "y": 100.5, "amt20": 229051751965}],
        "60": [...],
        "120": [...]
      }
    },
    "tpex": { "半導體業": { "20": [...], "60": [...], "120": [...] } }
  }
}
```

`amt20` 為 20 日均成交金額（元）。2026-07-18 實測 payload 約 147KB（53 個
「有個股對映」的產業 × 最多 3 週期 × 最多 10 檔），遠低於 500KB 控制目標。

### 每日更新

併入既有 `update-twse` job（台北時間 16:30）：抓上市／上櫃類股指數 →
抓上市＋上櫃個股收盤（`fetch_stocks.py --update`）→（僅週一）更新產業別
對照表 → 重算排行（`compute_stocks.py`），`data/stock_prices.csv`、
`data/stock_rankings.json`、`data/raw_stocks/` 一併 commit。

## 依賴

`requests`、`pandas`（皆為本機環境既有套件，未額外安裝）。全球面板的備援源
需要 `yfinance`（見上方〈資料源與備援〉），刻意不放進 `requirements.txt`。

## Streamlit 部署

雲端版 `streamlit_app.py` 讀 `data/sector_indices.csv`、（若存在）
`data/tpex_indices.csv` 與（若存在）`data/global_prices.csv`，重用
`compute_rrg.py`／`compute_tpex.py`／`compute_global.py` 的計算核心，提供
互動式 RRG 動畫（基準／週期／回放範圍／尾巴長度／族群篩選／面板切換）。
`web/rrg.html` 為另一位開發者維護的本機版頁面；雲端版把 `window.RRG_DATA`、
`window.RRG_DATASET_TPEX` 與 `window.RRG_DATASETS_GLOBAL` 動態算好後內嵌進
同一份 `rrg.html` 原始碼再用 `st.iframe` 整頁嵌入，兩邊共用同一份前端邏輯。

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
   16:30 自動抓當日證交所類股指數與櫃買中心上櫃類股指數（`update-twse`
   job），以及週一到週五台北時間隔日 06:00 自動抓全球資產／市場輪動資料
   （`update-global` job），皆 commit 回 repo；push 後 Streamlit Cloud 會
   偵測到 `data/` 變更並在下次讀取時反映（`st.cache_data` TTL 為 1 小時）。
   也可在 GitHub Actions 頁面手動 `Run workflow`，用 `target` 輸入選擇只
   回補台股（含上櫃）／只回補全球／兩者都跑。
4. Repo 的 Settings → Actions → General 需確認 Workflow permissions 允許
   「Read and write permissions」，否則 Actions 無法 push 更新（本 workflow
   已在 YAML 內宣告 `permissions: contents: write`，但部分機構帳號的預設
   repo 設定仍需手動開啟一次）。
