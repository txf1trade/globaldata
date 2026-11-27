"""
每日更新 indicators.json、headlines.json、meta.json（都放在 repo 根目錄）。

- 行情（股市指數、VIX、美債、黃金、比特幣、原油、美元、日圓）：Yahoo Finance（yfinance）
- 高收益債利差、美國2年期公債殖利率：FRED 公開 CSV（不需要 API key）
- 新聞：Google 新聞 RSS（繁體中文）
- 抓不到的指標會保留上一次的數值與日期，不會清空。
- 行事曆：期貨結算日依交易所休市曆自動推算；FOMC、CPI、非農日期從官方網站自動抓。
- 標記 "manual": true 的指標（例如台股融資、升息機率、AI 債務）沒有免費資料來源，不會被自動改寫，請手動編輯 indicators.json。
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
            tk = yf.Ticker(ticker)
            hist = tk.history(period="1y", auto_adjust=False)
            hist = hist.dropna(subset=["Close"])
            if len(hist) < 2:
                raise ValueError("資料不足")
            close = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            as_of = hist.index[-1].strftime("%Y-%m-%d")
            # Yahoo 的日線常常晚一天才補上亞洲市場當天那一根，用小時線補
            try:
                intraday = tk.history(period="5d", interval="60m", auto_adjust=False).dropna(subset=["Close"])
                if len(intraday):
                    last_day = intraday.index[-1].strftime("%Y-%m-%d")
                    if last_day > as_of:
                        prev = close
                        close = float(intraday["Close"].iloc[-1])
                        as_of = last_day
            except Exception as e:
                print(f"{i} 小時線讀取失敗，使用日線：{e}", file=sys.stderr)
            high = max(float(hist["Close"].max()), close)
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


# ---------- 行事曆：期貨結算日（交易所休市曆）與經濟數據日期（官方網站） ----------
from datetime import date

UA = {"User-Agent": "Mozilla/5.0 (market-risk-monitor; GitHub Actions)"}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _cal(code):
    """交易所休市曆；套件不可用時回傳 None，改用「週一到週五」近似。"""
    try:
        import exchange_calendars as xc
        today = datetime.now(TW).date()
        return xc.get_calendar(code, start=str(today - timedelta(days=40)), end=str(today + timedelta(days=400)))
    except Exception as e:
        print(f"{code} 休市曆無法使用，改用平日近似：{e}", file=sys.stderr)
        return None


def _is_session(cal, d):
    if cal is None:
        return d.weekday() < 5
    try:
        return bool(cal.is_session(d.isoformat()))
    except Exception:
        return d.weekday() < 5


def _shift(cal, d, forward=True):
    """遇休市：forward=True 順延到下一個交易日，False 提前到前一個交易日。"""
    step = timedelta(days=1 if forward else -1)
    for _ in range(15):
        if _is_session(cal, d):
            return d
        d += step
    return d


def _nth_weekday(y, m, weekday, n):
    d = date(y, m, 1)
    d = d + timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _nth_last_session(cal, y, m, n):
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    found = 0
    for _ in range(40):
        if _is_session(cal, d):
            found += 1
            if found == n:
                return d
        d -= timedelta(days=1)
    return d


def settlement_events(days_ahead=45):
    """依各交易所規則推算結算日，遇休市依規則順延或提前。"""
    today = datetime.now(TW).date()
    end = today + timedelta(days=days_ahead)
    tw, kr, jp, us = _cal("XTAI"), _cal("XKRX"), _cal("XTKS"), _cal("XNYS")
    out = []
    y, m = today.year, today.month
    for _ in range(3):
        ym = f"{m}月"
        quarter = m in (3, 6, 9, 12)
        # 台指期：第三個週三，遇休市順延
        d = _shift(tw, _nth_weekday(y, m, 2, 3), True)
        out.append({"id": f"auto_txf_{y}{m:02d}", "date": d.isoformat(), "tw_time": "13:30", "region": "台灣",
                    "event": f"台指期{ym}合約結算",
                    "why": "結算價取13:00–13:30平均，結算前多空攻防常使尾盤波動放大",
                    "source_url": "https://www.taifex.com.tw/cht/5/fSPCalendar"})
        # 富台指：當月倒數第二個台灣交易日
        d = _nth_last_session(tw, y, m, 2)
        out.append({"id": f"auto_sgx_{y}{m:02d}", "date": d.isoformat(), "tw_time": "13:30", "region": "台灣",
                    "event": f"富台指（SGX）{ym}合約結算",
                    "why": "結算價取台股收盤前25分鐘平均，外資期現貨部位調整可能放大尾盤波動",
                    "source_url": "https://www.sgx.com/derivatives/products/ftse-taiwan-index-futures"})
        # 日經225 SQ：第二個週五，遇休市提前
        d = _shift(jp, _nth_weekday(y, m, 4, 2), False)
        out.append({"id": f"auto_sq_{y}{m:02d}", "date": d.isoformat(), "tw_time": "08:00（開盤）", "region": "日本",
                    "event": "日經225 " + ("季度大結算（SQ）" if quarter else "月結算（SQ）"),
                    "why": "以開盤價結算，開盤前後常出現大額買賣單",
                    "source_url": "https://www.jpx.co.jp/english/derivatives/rules/last-trading-day/"})
        if quarter:
            # 美股四巫日：第三個週五，遇休市提前
            d = _shift(us, _nth_weekday(y, m, 4, 3), False)
            out.append({"id": f"auto_quad_{y}{m:02d}", "date": d.isoformat(), "tw_time": "22:30起", "region": "美國",
                        "event": "美股四巫日",
                        "why": "股指期貨、選擇權、個股期權同步到期，台道瓊／台那斯達克同日結算；收盤前一小時（台北隔天凌晨）波動最大",
                        "source_url": "https://www.cmegroup.com/trading/equity-index/"})
            # 韓股四巫日：第二個週四，遇休市提前
            d = _shift(kr, _nth_weekday(y, m, 3, 2), False)
            out.append({"id": f"auto_krquad_{y}{m:02d}", "date": d.isoformat(), "tw_time": "14:20", "region": "韓國",
                        "event": "韓股四巫日",
                        "why": "指數期貨、選擇權與個股期貨同步到期，可能加劇外資進出與尾盤波動",
                        "source_url": "https://global.krx.co.kr/"})
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return [e for e in out if today.isoformat() <= e["date"] <= end.isoformat()]


_MONTHS = {k: i + 1 for i, k in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _us_to_tw(d, hhmm):
    """美東時間 → 台灣時間（自動處理夏令時間）。"""
    from zoneinfo import ZoneInfo
    h, mi = hhmm
    t = datetime(d.year, d.month, d.day, h, mi, tzinfo=ZoneInfo("America/New_York")).astimezone(TW)
    return t.date(), t.strftime("%H:%M")


def parse_fomc(html):
    """解析 Fed 官網 FOMC 行程，回傳 [(會議最後一天 date, 文字)]。"""
    import re
    out = []
    parts = re.split(r"(\d{4}) FOMC Meetings", html)
    for i in range(1, len(parts) - 1, 2):
        year = int(parts[i])
        body = parts[i + 1]
        for mon, days in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?fomc-meeting__date[^>]*>\s*([\d\-]+)', body, re.S):
            last_mon = mon.split("/")[-1][:3].lower()
            if last_mon not in _MONTHS:
                continue
            last_day = int(days.split("-")[-1])
            try:
                out.append((date(year, _MONTHS[last_mon], last_day), f"{mon} {days}"))
            except ValueError:
                pass
    return out


def parse_bls(html):
    """解析 BLS 公布行程表，回傳 [(參考月份文字, 公布日 date, (時, 分))]。"""
    import re
    out = []
    for ref, rel, tm in re.findall(r"<td>\s*([A-Z][a-z]{2,4}\.?\s+\d{4})\s*</td>\s*<td>\s*([A-Z][a-z]{2,4}\.?\s+\d{1,2},\s+\d{4})\s*</td>\s*<td>\s*(\d{1,2}:\d{2}\s*[AP]M)\s*</td>", html):
        mon, day, year = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})", rel).groups()
        h, mi = map(int, re.match(r"(\d{1,2}):(\d{2})", tm).groups())
        if "PM" in tm and h != 12:
            h += 12
        try:
            out.append((ref, date(int(year), _MONTHS[mon[:3].lower()], int(day)), (h, mi)))
        except (KeyError, ValueError):
            pass
    return out


def _ref_month_zh(ref):
    import re
    m = re.match(r"([A-Za-z]+)", ref)
    return f"{_MONTHS.get(m.group(1)[:3].lower(), '')}月" if m else ""


def econ_events(days_ahead=45):
    """從官方網站抓 FOMC、CPI、非農的日期；抓不到就略過（保留手動資料）。"""
    today = datetime.now(TW).date()
    end = today + timedelta(days=days_ahead)
    out = []
    try:
        for d, label in parse_fomc(_get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")):
            twd, twt = _us_to_tw(d, (14, 0))
            out.append({"id": f"auto_fomc_{d.isoformat()}", "date": twd.isoformat(), "tw_time": twt, "region": "美國",
                        "event": f"FOMC利率決議（美東{d.month}/{d.day}）",
                        "why": "聯準會利率決策，左右全球資金成本與股債走勢",
                        "source_url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"})
    except Exception as e:
        print(f"FOMC 行程抓取失敗：{e}", file=sys.stderr)
    for url, name, why in [
        ("https://www.bls.gov/schedule/news_release/cpi.htm", "CPI", "通膨是否降溫，決定後續利率方向"),
        ("https://www.bls.gov/schedule/news_release/empsit.htm", "非農就業報告", "就業太強→升息壓力；太弱→衰退疑慮"),
    ]:
        try:
            for ref, d, hm in parse_bls(_get(url)):
                twd, twt = _us_to_tw(d, hm)
                out.append({"id": f"auto_{'cpi' if name == 'CPI' else 'nfp'}_{d.isoformat()}", "date": twd.isoformat(),
                            "tw_time": twt, "region": "美國", "event": f"{_ref_month_zh(ref)}{name}",
                            "why": why, "source_url": url})
        except Exception as e:
            print(f"{name} 行程抓取失敗：{e}", file=sys.stderr)
    return [e for e in out if today.isoformat() <= e["date"] <= end.isoformat()]


HOLIDAY_ZH = {
    "Thanksgiving": "感恩節", "Christmas": "聖誕節", "New Year's Day": "元旦", "Dr. Martin Luther King Jr. Day": "金恩紀念日",
    "President's Day": "總統日", "Presidents' Day": "總統日", "Good Friday": "耶穌受難日", "Memorial Day": "陣亡將士紀念日",
    "Juneteenth": "六月節", "Independence Day": "獨立紀念日", "Labor Day": "勞動節", "Labour Day": "勞動節",
    "Constitution Day": "行憲紀念日", "Peace Memorial Day": "和平紀念日", "Children's Day": "兒童節",
    "Tomb Sweeping Day": "清明節", "Dragon Boat Festival": "端午節", "Mid-Autumn Festival": "中秋節", "National Day": "國慶日",
}


def holiday_events(days_ahead=45):
    """依交易所休市曆列出美股、台股休市日與美股提前收盤日。"""
    today = datetime.now(TW).date()
    end = today + timedelta(days=days_ahead)
    out = []
    for code, region, label, why in [
        ("XNYS", "美國", "美股", "美股休市，台股與台指期夜盤少了美股方向，流動性較低、隔天容易反映兩天消息"),
        ("XTAI", "台灣", "台股", "台股與台指期休市，留意休市期間國際市場變化，復市開盤可能跳空"),
    ]:
        cal = _cal(code)
        if cal is None:
            continue
        names = {}
        try:
            h = cal.regular_holidays.holidays(start=str(today), end=str(end), return_name=True)
            names = {d.date(): n for d, n in h.items()}
        except Exception:
            pass
        d = today
        while d <= end:
            if d.weekday() < 5 and not _is_session(cal, d):
                zh = HOLIDAY_ZH.get(names.get(d, ""), "")
                out.append({"id": f"auto_hol_{code}_{d.isoformat()}", "date": d.isoformat(), "tw_time": "全日", "region": region,
                            "event": f"{label}休市" + (f"（{zh}）" if zh else ""), "why": why,
                            "source_url": "https://www.nyse.com/markets/hours-calendars" if code == "XNYS" else "https://www.twse.com.tw/zh/trading/holiday.html"})
            d += timedelta(days=1)
        if code == "XNYS":
            try:
                ec = cal.early_closes
                for ts in ec[(ec >= str(today)) & (ec <= str(end))]:
                    d = ts.date()
                    twd, twt = _us_to_tw(d, (13, 0))
                    out.append({"id": f"auto_early_{d.isoformat()}", "date": d.isoformat(), "tw_time": "提前收盤", "region": "美國",
                                "event": "美股提前收盤", "why": f"美東13:00收盤（台北{twd.month}/{twd.day} {twt}），交易量少、價格容易大幅波動",
                                "source_url": "https://www.nyse.com/markets/hours-calendars"})
            except Exception as e:
                print(f"提前收盤日讀取失敗：{e}", file=sys.stderr)
    return out


# ---------- 數據事件的「怎麼看」與前值 ----------
GUIDES = [
    ("東京CPI", "日本通膨領先指標；高於預估＝日銀12月升息機率提高，日圓走強"),
    ("會議紀錄", "內容偏鷹（多數委員支持續升息）＝股市承壓；偏鴿（討論暫停升息）＝利多"),
    ("非農", "新增就業高於預估且超過+10萬＝偏強，升息壓力加大；0～+5萬＝持平（目前就業損益平衡約+0.5萬人）；負值或失業率升破4.3%＝偏弱，衰退疑慮升高"),
    ("CPI", "看核心CPI月增：0.2%以下＝降溫（利多股市）；0.3%＝偏熱；0.4%以上＝過熱，升息壓力加大。公布值高於預估通常不利股市"),
    ("PCE", "Fed最看重的通膨指標，目標2%。核心PCE月增0.2%以下＝降溫；0.3%以上＝偏熱，支持續升息"),
    ("PPI", "生產端物價，月增0.2%以下＝溫和；0.4%以上＝成本壓力升溫，之後可能轉嫁到CPI"),
    ("JOLTS", "職缺比前值減少＝勞動市場降溫（有助通膨回落）；明顯增加＝勞動市場仍緊，升息壓力"),
    ("ISM", "50以上＝擴張、50以下＝收縮；物價分項高於60代表通膨壓力仍大"),
    ("零售", "月增高於0.5%＝消費強勁，升息壓力；0以下＝消費轉弱，景氣放緩"),
    ("FOMC", "照預期升1碼＝影響有限；升2碼或暗示明年多次升息＝偏鷹，股市承壓；不升息或語氣轉鴿＝利多股市"),
    ("日銀", "升息＝日圓走強，可能引發日圓套利交易平倉、全球股市賣壓；按兵不動＝日圓偏弱、壓力暫緩"),
    ("歐洲央行", "升息或偏鷹＝全球利率維持高檔；降息或轉鴿＝美元相對走強"),
    ("英國央行", "升息或偏鷹＝全球利率維持高檔；降息或轉鴿＝英鎊走弱"),

]

def guide_for(event):
    for key, text in GUIDES:
        if key in event:
            return text
    return ""


def _fred_series(series):
    text = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}")
    return [(d, float(v)) for d, v in csv.reader(io.StringIO(text)) if d[:1].isdigit() and v not in ("", ".")]


def latest_prev_values():
    """從 FRED 算出最新一期的 CPI 月增、核心 CPI 月增、非農新增與失業率，當作「前值」。"""
    out = {}
    try:
        cpi, core = _fred_series("CPIAUCSL"), _fred_series("CPILFESL")
        m = int(cpi[-1][0][5:7])
        out["CPI"] = f"{m}月月增{(cpi[-1][1] / cpi[-2][1] - 1) * 100:.1f}%、核心{(core[-1][1] / core[-2][1] - 1) * 100:.1f}%"
    except Exception as e:
        print(f"CPI 前值計算失敗：{e}", file=sys.stderr)
    try:
        pay, ur = _fred_series("PAYEMS"), _fred_series("UNRATE")
        m = int(pay[-1][0][5:7])
        chg = (pay[-1][1] - pay[-2][1]) / 10  # 千人 → 萬人
        out["非農"] = f"{m}月{chg:+.1f}萬（失業率{ur[-1][1]:.1f}%）"
    except Exception as e:
        print(f"非農前值計算失敗：{e}", file=sys.stderr)
    return out


def enrich_events(rows):
    """替還沒有「怎麼看」的數據事件補上判斷標準；CPI、非農補上前值。手動填的內容一律保留。"""
    prev = None
    for r in rows:
        ev = str(r.get("event", ""))
        if not r.get("guide"):
            g = guide_for(ev)
            if g:
                r["guide"] = g
        key = "CPI" if ("CPI" in ev and r.get("region") == "美國") else "非農" if "非農" in ev else None
        if key and not r.get("prev"):
            if prev is None:
                prev = latest_prev_values()
            if prev.get(key):
                r["prev"] = prev[key]
    return rows


DUP_KEYS = ("結算", "四巫", "SQ", "FOMC", "CPI", "非農", "休市", "提前收盤")


def update_calendar():
    path = DATA / "calendar.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    except Exception:
        rows = []
    today = datetime.now(TW).date()
    keep = [r for r in rows if not str(r.get("id", "")).startswith("auto_")
            and str(r.get("date", "")) >= (today - timedelta(days=2)).isoformat()]

    def dup(e):
        key = next((k for k in DUP_KEYS if k in e["event"]), None)
        return any(r.get("date") == e["date"] and r.get("region") == e["region"]
                   and key and key in str(r.get("event", "")) for r in keep)

    auto = settlement_events() + econ_events() + holiday_events()
    rows = keep + [e for e in auto if not dup(e)]
    rows = enrich_events(rows)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("tw_time", "")))
    path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 原本手動的指標：能自動的改成自動 ----------
def fetch_cpi(rows_by_id):
    """美國 CPI 年增率：FRED CPIAUCSL，自行計算年增率。"""
    try:
        text = _get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL")
        pts = [(d, float(v)) for d, v in csv.reader(io.StringIO(text)) if d[:1].isdigit() and v not in ("", ".")]
        d1, v1 = pts[-1]
        _, v0 = pts[-13]
        _, vp = pts[-2]
        _, vp0 = pts[-14]
        yoy, prev_yoy = (v1 / v0 - 1) * 100, (vp / vp0 - 1) * 100
        m = int(d1[5:7])
        rows_by_id["us_cpi"] = {
            "id": "us_cpi", "group": "利率與央行", "name": f"美國CPI年增率（{m}月）", "value": round(yoy, 1), "unit": "%",
            "change": round((yoy - prev_yoy) * 100), "change_unit": "bp", "as_of": d1,
            "signal": "red" if yoy >= 4 else "yellow" if yoy >= 3 else "green",
            "note": "通膨越高，聯準會越難降息；3%以上屬偏高",
            "source_name": "FRED", "source_url": "https://fred.stlouisfed.org/series/CPIAUCSL"}
    except Exception as e:
        print(f"CPI 更新失敗，保留舊值：{e}", file=sys.stderr)



def main():
    ind_path = DATA / "indicators.json"
    old = json.loads(ind_path.read_text(encoding="utf-8"))["rows"]
    rows_by_id = {r["id"]: r for r in old}
    order = [r["id"] for r in old]

    fetch_yahoo(rows_by_id)
    fetch_fred(rows_by_id)
    fetch_cpi(rows_by_id)

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

    try:
        update_calendar()
    except Exception as e:
        print(f"行事曆更新失敗：{e}", file=sys.stderr)

    (DATA / "meta.json").write_text(json.dumps(
        {"updated_at": datetime.now(TW).strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False), encoding="utf-8")
    print("更新完成")


if __name__ == "__main__":
    main()
