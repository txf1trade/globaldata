# 全球市場風險監控

網址：https://txf1trade.github.io/globaldata/

用紅／黃／綠燈追蹤全球股市、恐慌與流動性、利率、避險資產等指標，附重要數據公布行事曆與新聞。數據由 GitHub Actions 每天自動更新兩次（台灣時間 06:10、14:10）。

## 檔案

```
.github/workflows/update.yml   自動更新排程（必須放在這個資料夾）
index.html                     網頁
indicators.json                指標數據與燈號
calendar.json                  數據公布與央行會議行事曆（手動維護）
headlines.json                 新聞（自動更新）
meta.json                      最後更新時間
update_data.py                 抓數據、判斷燈號的程式
requirements.txt               程式需要的套件
```

## 設定（只做一次）

1. Settings → Pages：Branch 選 `main`、資料夾 `/ (root)`。
2. Settings → Actions → General → Workflow permissions：選「Read and write permissions」。
3. Actions → 更新市場數據 → Run workflow，先手動跑一次。

## 手動維護

`indicators.json` 中標記 `"manual": true` 的指標（跌漲家數比、台股上漲占比、台股融資、升息機率、日本10年債、美國CPI、AI企業債務）不會自動更新，請直接編輯。`calendar.json` 請定期補上新事件。

本專案為市場數據與新聞整理，不構成投資建議。
