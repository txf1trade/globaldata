# 全球市場風險監控

網址：https://txf1trade.github.io/globaldata/

用紅／黃／綠燈追蹤全球股市、恐慌與流動性、利率、避險資產等指標，附數據公布、央行會議與期貨結算行事曆。由 GitHub Actions 每天自動更新兩次（台灣時間 06:10、14:10），也可以在 Actions 頁面手動執行。

## 檔案

```
.github/workflows/update.yml   自動更新排程（必須放在這個資料夾）
index.html                     網頁
indicators.json                指標數據與燈號
calendar.json                  行事曆
headlines.json                 新聞
meta.json                      最後更新時間
update_data.py                 抓數據、判斷燈號、整理行事曆的程式
requirements.txt               程式需要的套件
```

## 自動更新的內容

| 項目 | 來源 |
|---|---|
| 股市指數、VIX、美債10年、黃金、比特幣、原油、美元、日圓 | Yahoo Finance |
| 高收益債利差、美債2年、美國CPI年增率 | FRED |
| 新聞 | Google 新聞 RSS |
| 期貨結算日（台指期、富台指、美股／韓股四巫日、日經SQ） | 依交易所規則推算，遇休市依各交易所休市曆順延或提前 |
| FOMC、美國CPI、非農就業公布日 | 聯準會、美國勞工統計局官網 |

## 需要手動維護的內容

- `indicators.json` 中標記 `"manual": true` 的指標（跌漲家數比、台股上漲占比、台股融資、升息機率、日本10年債、AI企業債務）沒有免費資料來源。超過 14 天沒更新，網頁上會自動變淡提醒。
- 其他央行會議（日銀、歐洲央行、英國央行）、PCE、PPI 等事件，可直接在 `calendar.json` 補上。程式只會替換 id 開頭為 `auto_` 的項目，手動加的會保留，過期兩天後自動刪除。

## 設定（只做一次）

1. Settings → Pages：Branch 選 `main`、資料夾 `/ (root)`。
2. Settings → Actions → General → Workflow permissions：選「Read and write permissions」。
3. Actions → 更新市場數據 → Run workflow，先手動跑一次。

## 長期維護

- 結算日與休市日來自 `exchange_calendars` 套件，每次執行都會安裝最新版，新的年度休市日會由套件維護者更新。
- 官方網站改版導致抓不到日期時，程式會略過該項並保留手動資料，不會中斷其他更新。Actions 的執行紀錄會列出失敗原因。

本專案為市場數據與新聞整理，不構成投資建議。
