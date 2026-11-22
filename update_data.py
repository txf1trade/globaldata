"""
每日更新 indicators.json、headlines.json、meta.json（都放在 repo 根目錄）。

- 行情（股市指數、VIX、美債、黃金、比特幣、原油、美元、日圓）：Yahoo Finance（yfinance）
- 高收益債利差、美國2年期公債殖利率：FRED 公開 CSV（不需要 API key）
- 新聞：Google 新聞 RSS（繁體中文）
- 抓不到的指標會保留上一次的數值與日期，不會清空。
- 標記 "manual": true 的指標（例如台股融資、升息機率、AI 債務）不會被自動改寫，請手動編輯 indicators.json。
"""
import csv
import io
import json
import math
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT
TW = timezone(timedelta(hours=8))

# id -> (Yahoo 代碼, 名稱, 類別, 單位, 說明)
YAHOO = {
    "spx":    ("^GSPC",    "標普500",            "股市", "點", "美股大盤；距一年高點跌逾10%即屬「修正」"),
    "ixic":   ("^IXIC",    "那斯達克",            "股市", "點", "科技股權重高，最能反映AI題材的資金進出"),
    "sox":    ("^SOX",     "費城半導體",          "股市", "點", "AI晶片股是本輪賣壓核心，台股連動高"),
    "twii":   ("^TWII",    "台股加權",            "股市", "點", "台股高度集中在電子與AI供應鏈"),
    "n225":   ("^N225",    "日經225",             "股市", "點", "日本股市；也會受日圓與日銀升息影響"),
    "kospi":  ("^KS11",    "韓股KOSPI",           "股市", "點", "記憶體與半導體權重高"),
    "vix":    ("^VIX",     "VIX恐慌指數",         "恐慌與流動性", "", "30以上屬高度恐慌，代表避險需求急升"),
    "us10y":  ("^TNX",     "美國10年期公債殖利率", "利率與央行", "%", "全球借貸成本基準；5%是市場關注的穩定門檻"),
    "dxy":    ("DX-Y.NYB", "美元指數",            "利率與央行", "", "美元走強會把資金從新興市場抽走"),
    "usdjpy": ("JPY=X",    "美元兌日圓",          "利率與央行", "", "逼近160時日本可能干預；日圓急升會引發套利交易平倉"),
    "gold":   ("GC=F",     "黃金期貨",            "避險與商品", "美元", "若和股市一起跌，代表投資人被迫賣資產換現金"),
    "btc":    ("BTC-USD",  "比特幣",              "避險與商品", "美元", "高槓桿資產，常最先反映流動性收縮"),
    "wti":    ("CL=F",     "WTI原油",             "避險與商品", "美元", "油價高會推升通膨、讓央行難以放鬆"),
}

# id -> (FRED 序列, 名稱, 類別, 單位, 說明)
FRED = {
    "hy_oas": ("BAMLH0A0HYM2", "美國高收益債利差", "恐慌與流動性", "%", "利差擴大代表企業借錢變貴、信用市場緊縮"),
    "us2y":   ("DGS2",         "美國2年期公債殖利率", "利率與央行", "%", "反映市場對聯準會升降息的預期"),
}

NEWS_QUERIES = [
    "全球股市 流動性",
    "聯準會 升息",
    "美債殖利率",
    "AI 股 拋售",
    "台股 外資 賣超",
]


def worst(*signals):
    order = {"red": 0, "yellow": 1, "green": 2}
    return min(signals, key=lambda s: order.get(s, 3))


def signal_for(i, value, change, drawdown):
    """燈號規則：red=壓力明顯、yellow=注意、green=正常。可依自己的判斷修改門檻。"""
    s = "green"
    if YAHOO.get(i, (None, None, ""))[2] == "股市":
        if change is not None:
            s = worst(s, "red" if change <= -3 else "yellow" if change <= -1.5 else "green")
        if drawdown is not None:
            s = worst(s, "red" if drawdown <= -10 else "yellow" if drawdown <= -5 else "green")
    elif i == "vix":
        s = "red" if value >= 30 else "yellow" if value >= 20 else "green"
    elif i == "us10y":
        s = "red" if value >= 5.0 else "yellow" if value >= 4.6 else "green"
    elif i == "us2y":
        s = "yellow" if value >= 4.5 else "green"
    elif i == "dxy":
        s = "red" if value >= 106 else "yellow" if value >= 103 else "green"
    elif i == "usdjpy":
        s = "red" if value >= 160 else "yellow" if value >= 155 else "green"
    elif i == "wti":
        s = "red" if value >= 100 else "yellow" if value >= 90 else "green"
    elif i == "gold":
        s = "yellow" if (change is not None and change <= -2) else "green"
    elif i == "btc":
        if change is not None:
            s = "red" if change <= -5 else "yellow" if change <= -3 else "green"
        if drawdown is not None:
            s = worst(s, "red" if drawdown <= -30 else "yellow" if drawdown <= -20 else "green")
    elif i == "hy_oas":
        s = "red" if value >= 5 else "yellow" if value >= 4 else "green"
    return s


def fetch_yahoo(rows_by_id):
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance 未安裝，略過行情更新", file=sys.stderr)
        return
    for i, (ticker, name, group, unit, note) in YAHOO.items():
        try:
            hist = yf.Ticker(ticker).history(period="1y", auto_adjust=False)
            hist = hist.dropna(subset=["Close"])
            if len(hist) < 2:
                raise ValueError("資料不足")
            close = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            high = float(hist["Close"].max())
            as_of = hist.index[-1].strftime("%Y-%m-%d")
            if i == "us10y":
                if close > 20:  # 舊格式 ×10
                    close, prev = close / 10, prev / 10
                change, change_unit = round((close - prev) * 100), "bp"
            else:
                change, change_unit = round((close / prev - 1) * 100, 2), "%"
            drawdown = (close / high - 1) * 100 if high else None
            value = round(close, 2)
            extra = ""
            if drawdown is not None and drawdown <= -5 and group in ("股市", "避險與商品"):
                extra = f"；距一年高點 {drawdown:.1f}%"
            rows_by_id[i] = {
                "id": i, "group": group, "name": name, "value": value, "unit": unit,
                "change": change, "change_unit": change_unit, "as_of": as_of,
                "signal": signal_for(i, value, change if change_unit == "%" else None, drawdown),
                "note": note + extra,
                "source_name": "Yahoo Finance",
                "source_url": f"https://finance.yahoo.com/quote/{urllib.request.quote(ticker)}",
            }
        except Exception as e:  # 保留舊值
            print(f"{i} ({ticker}) 更新失敗，保留舊值：{e}", file=sys.stderr)

    # 黃金與股市同跌 → 流動性壓力紅燈
    eq_red = sum(1 for k in ("spx", "ixic", "twii") if rows_by_id.get(k, {}).get("signal") == "red")
    g = rows_by_id.get("gold")
    if g and g.get("change") is not None and g["change"] <= -2 and eq_red >= 2:
        g["signal"] = "red"


def fetch_fred(rows_by_id):
    for i, (series, name, group, unit, note) in FRED.items():
        try:
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
            with urllib.request.urlopen(url, timeout=30) as r:
                text = r.read().decode("utf-8")
            pts = [(d, v) for d, v in csv.reader(io.StringIO(text)) if v not in ("", ".") and d[:1].isdigit()]
            d1, v1 = pts[-1]
            d0, v0 = pts[-2]
            value, prev = float(v1), float(v0)
            rows_by_id[i] = {
                "id": i, "group": group, "name": name, "value": round(value, 2), "unit": unit,
                "change": round((value - prev) * 100), "change_unit": "bp", "as_of": d1,
                "signal": signal_for(i, value, None, None), "note": note,
                "source_name": "FRED", "source_url": f"https://fred.stlouisfed.org/series/{series}",
            }
        except Exception as e:
            print(f"{i} ({series}) 更新失敗，保留舊值：{e}", file=sys.stderr)


def fetch_news(old_rows):
    try:
        import feedparser
    except ImportError:
        print("feedparser 未安裝，略過新聞更新", file=sys.stderr)
        return old_rows
    seen, out = set(), []
    cutoff = datetime.now(TW) - timedelta(days=7)
    for q in NEWS_QUERIES:
        url = ("https://news.google.com/rss/search?q=" + urllib.request.quote(q + " when:7d")
               + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"新聞 {q} 失敗：{e}", file=sys.stderr)
            continue
        for e in feed.entries[:4]:
            title = e.get("title", "").strip()
            link = e.get("link", "")
            if not title or not link.startswith("http") or title in seen:
                continue
            try:
                dt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).astimezone(TW)
            except Exception:
                dt = datetime.now(TW)
            if dt < cutoff:
                continue
            src = e.get("source", {}).get("title", "") if isinstance(e.get("source"), dict) else ""
            if src and title.endswith(" - " + src):
                title = title[: -len(" - " + src)]
            seen.add(title)
            out.append({"id": f"n{len(out)+1}", "date": dt.strftime("%Y-%m-%d"), "topic": q.split()[0],
                        "title": title, "source_name": src or "Google 新聞", "source_url": link})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out[:12] if out else old_rows


def main():
    ind_path = DATA / "indicators.json"
    old = json.loads(ind_path.read_text(encoding="utf-8"))["rows"]
    rows_by_id = {r["id"]: r for r in old}
    order = [r["id"] for r in old]

    fetch_yahoo(rows_by_id)
    fetch_fred(rows_by_id)

    for i in list(YAHOO) + list(FRED):
        if i not in order:
            order.append(i)
    rows = [rows_by_id[i] for i in order if i in rows_by_id]
    for r in rows:
        for k in ("value", "change"):
            if isinstance(r.get(k), float) and not math.isfinite(r[k]):
                r[k] = None
    ind_path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")

    news_path = DATA / "headlines.json"
    old_news = json.loads(news_path.read_text(encoding="utf-8"))["rows"]
    news_path.write_text(json.dumps({"rows": fetch_news(old_news)}, ensure_ascii=False, indent=1), encoding="utf-8")

    (DATA / "meta.json").write_text(json.dumps(
        {"updated_at": datetime.now(TW).strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False), encoding="utf-8")
    print("更新完成")


if __name__ == "__main__":
    main()
