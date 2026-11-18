#!/usr/bin/env python3
"""Collects company news, SEC filings and prices, scores each story by how much it
can move model assumptions, and writes JSON for the static site. Standard library only."""
import csv, hashlib, io, json, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
UA = "Mozilla/5.0 (compatible; tech-news-desk/1.0; +https://github.com/gladky06/tech-news-desk)"
ATOM = "{http://www.w3.org/2005/Atom}"


SEC_UA = "tech-news-desk gladky06@users.noreply.github.com"


def http_get(url, timeout=25):
    ua = SEC_UA if "sec.gov" in url else UA
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"), ("&lt;", "<"), ("&gt;", ">")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(xml_bytes, default_source=""):
    """Parses RSS 2.0 or Atom. Returns dicts with title, url, source, publishedAt (datetime), summary."""
    out = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or default_source).strip()
        if src and title.endswith(" - " + src):  # Google News appends the publisher
            title = title[: -len(src) - 3].strip()
        out.append({"title": title, "url": (it.findtext("link") or "").strip(), "source": src,
                    "publishedAt": parse_date(it.findtext("pubDate")),
                    "summary": strip_html(it.findtext("description"))[:300]})
    for en in root.iter(ATOM + "entry"):
        link = ""
        for l in en.findall(ATOM + "link"):
            if l.get("rel") in (None, "alternate"):
                link = l.get("href") or ""
                break
        out.append({"title": (en.findtext(ATOM + "title") or "").strip(), "url": link, "source": default_source,
                    "publishedAt": parse_date(en.findtext(ATOM + "updated") or en.findtext(ATOM + "published")),
                    "summary": strip_html(en.findtext(ATOM + "summary") or en.findtext(ATOM + "content"))[:300]})
    return [o for o in out if o["title"] and o["url"]]


def google_news_url(query):
    q = urllib.parse.quote_plus(query + " when:2d")
    return "https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en" % q


def sec_url(cik):
    return ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%s"
            "&type=&dateb=&owner=include&count=40&output=atom" % cik)


IMPORTANT_FORMS = {"8-K": 7, "10-Q": 8, "10-K": 8, "DEF 14A": 3, "S-3": 5, "S-3ASR": 5, "424B2": 5, "424B5": 5, "SC 13D": 6, "SC 13G": 2}


def parse_sec(xml_bytes, company):
    out = []
    root = ET.fromstring(xml_bytes)
    for en in root.iter(ATOM + "entry"):
        form = ""
        cat = en.find(ATOM + "category")
        if cat is not None:
            form = cat.get("term") or ""
        if form not in IMPORTANT_FORMS:
            continue
        link = en.find(ATOM + "link")
        out.append({"title": "%s filed %s with the SEC" % (company["name"], form),
                    "url": link.get("href") if link is not None else "", "source": "SEC EDGAR",
                    "publishedAt": parse_date(en.findtext(ATOM + "updated")),
                    "summary": strip_html(en.findtext(ATOM + "summary"))[:300], "form": form})
    return [o for o in out if o["url"]]


# ---------- scoring ----------

def norm_title(t):
    return re.sub(r"[^a-z0-9 ]+", "", t.lower()).strip()


STOP = set("the a an of to in on for and or as at by with from is are was its it this that after amid over new says say said will report reports reported ignite 2026".split())


def tokens(t):
    return {w for w in norm_title(t).split() if w not in STOP and len(w) > 2}


def score_item(item, company, cfg):
    text = (item["title"] + " " + item.get("summary", "")).lower()
    title = item["title"].lower()
    drivers, score = [], 0
    for d in cfg["drivers"]:
        if any(re.search(p, text) for p in d["patterns"]):
            drivers.append(d["id"])
    drivers.sort(key=lambda i: -next(d["weight"] for d in cfg["drivers"] if d["id"] == i))
    weights = [next(d["weight"] for d in cfg["drivers"] if d["id"] == i) for i in drivers]
    if weights:  # strongest driver counts in full, the rest add half
        score = weights[0] + sum(w * 0.5 for w in weights[1:3])
    if re.search(r"\$\s?\d+(\.\d+)?\s?(billion|bn|b\b)", text):
        score += 2
    elif re.search(r"\$\s?\d+(\.\d+)?\s?(million|m\b)", text):
        score += 0.5
    in_title = any(re.search(r"\b%s\b" % re.escape(a), title) for a in company["aliases"])
    if in_title:
        score += 1
    else:
        score -= 2
    noise = any(re.search(p, title) for p in cfg["noise_patterns"])
    if noise:
        score -= 4
    if item.get("form"):
        score = IMPORTANT_FORMS[item["form"]]
        drivers = drivers or ["guidance"]
    return round(score, 1), drivers, noise


def level(score, cfg):
    th = cfg["thresholds"]
    return "high" if score >= th["high"] else "medium" if score >= th["medium"] else "low"


def cluster(items, skip=frozenset()):
    """Merges stories with the same or very similar headline. Keeps the earliest, counts sources."""
    items = sorted(items, key=lambda x: x["publishedAt"])
    groups = []
    for it in items:
        tk = tokens(it["title"])
        hit = None
        for g in groups:
            if abs((it["publishedAt"] - g["publishedAt"]).total_seconds()) > 36 * 3600:
                continue
            inter = len(tk & g["_tk"])
            union = len(tk | g["_tk"]) or 1
            core = len((tk & g["_tk"]) - skip)
            small = min(len(tk - skip), len(g["_tk"] - skip)) or 1
            if norm_title(it["title"]) == norm_title(g["title"]) or inter / union >= 0.5 or (core >= 3 and core / small >= 0.35):
                hit = g
                break
        if hit:
            hit["_members"].add(norm_title(it["title"]))
            if it["source"] and it["source"] not in hit["sources"]:
                hit["sources"].append(it["source"])
        else:
            it["_tk"] = tk
            it["_members"] = {norm_title(it["title"])}
            it["sources"] = [it["source"]] if it["source"] else []
            groups.append(it)
    return groups


# ---------- prices ----------

def fetch_price(ticker):
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1y&interval=1d" % ticker
        res = json.loads(http_get(url))["chart"]["result"][0]
        ts, closes = res["timestamp"], res["indicators"]["quote"][0]["close"]
        rows = [(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), round(c, 2)) for t, c in zip(ts, closes) if c]
        src = "Yahoo Finance"
    except Exception as e1:
        raw = http_get("https://stooq.com/q/d/l/?s=%s.us&i=d" % ticker.lower()).decode()
        rows = [(r["Date"], round(float(r["Close"]), 2)) for r in csv.DictReader(io.StringIO(raw)) if r.get("Close")][-260:]
        src = "Stooq"
        if not rows:
            raise RuntimeError("no price rows (%s)" % e1)
    return price_summary(rows, src)


def price_summary(rows, src):
    last_d, last = rows[-1]
    def chg(n):
        return round((last / rows[-1 - n][1] - 1) * 100, 2) if len(rows) > n else None
    year = last_d[:4]
    prev_year = [c for d, c in rows if d[:4] < year]
    return {"price": last, "date": last_d, "source": src, "d1": chg(1), "d5": chg(5), "m1": chg(21),
            "ytd": round((last / prev_year[-1] - 1) * 100, 2) if prev_year else None,
            "series": [{"d": d, "c": c} for d, c in rows[-45:]]}


# ---------- main ----------

def load(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def build(cfg, fetch=http_get, now=None):
    now = now or datetime.now(timezone.utc)
    old = load(DATA / "news.json", {"items": []})
    by_id = {i["id"]: i for i in old.get("items", [])}
    status = {"updatedAt": iso(now), "sources": [], "prices": load(DATA / "status.json", {}).get("prices", {})}
    fresh, absorbed = [], set()
    for co in cfg["companies"]:
        feeds = [("Google News: " + q, google_news_url(q), "rss") for q in co["queries"]]
        feeds += [(u, u, "rss") for u in co.get("feeds", [])]
        if co.get("cik"):
            feeds.append(("SEC EDGAR", sec_url(co["cik"]), "sec"))
        raw = []
        for name, url, kind in feeds:
            try:
                data = fetch(url)
                got = parse_sec(data, co) if kind == "sec" else parse_feed(data, default_source=co["name"] + " blog" if "Google" not in name else "")
                raw += got
                status["sources"].append({"name": name, "ticker": co["ticker"], "ok": True, "count": len(got)})
            except Exception as e:
                status["sources"].append({"name": name, "ticker": co["ticker"], "ok": False, "error": str(e)[:160]})
            time.sleep(0.4)
        raw = [r for r in raw if r["publishedAt"] and r["publishedAt"] > now - timedelta(days=4) and r["publishedAt"] <= now + timedelta(hours=1)]
        for it in cluster(raw, frozenset(co["aliases"])):
            sc, drivers, noise = score_item(it, co, cfg)
            if len(it["sources"]) >= 3:
                sc += 1
            if len(it["sources"]) >= 6:
                sc += 1
            if noise or sc < 0:
                continue  # listicles, how-tos and off-topic stories are dropped
            iid = hashlib.sha1((co["ticker"] + norm_title(it["title"])).encode()).hexdigest()[:14]
            for m in it["_members"]:  # ids of headlines folded into this story
                mid = hashlib.sha1((co["ticker"] + m).encode()).hexdigest()[:14]
                if mid != iid:
                    absorbed.add(mid)
            fresh.append({"id": iid, "ticker": co["ticker"], "title": it["title"], "url": it["url"],
                          "source": it["sources"][0] if it["sources"] else "", "sources": it["sources"][:8],
                          "sourceCount": len(it["sources"]), "publishedAt": iso(it["publishedAt"]),
                          "summary": it.get("summary", ""), "drivers": drivers, "score": sc,
                          "materiality": level(sc, cfg), "form": it.get("form", "")})
        try:
            status["prices"][co["ticker"]] = fetch_price(co["ticker"]) if fetch is http_get else status["prices"].get(co["ticker"], {})
        except Exception as e:
            status["sources"].append({"name": "Price", "ticker": co["ticker"], "ok": False, "error": str(e)[:160]})
    for it in fresh:
        prev = by_id.get(it["id"])
        if prev:  # keep first-seen time and the widest source list
            it["firstSeen"] = prev.get("firstSeen", iso(now))
            merged = list(dict.fromkeys(prev.get("sources", []) + it["sources"]))[:8]
            it["sources"], it["sourceCount"] = merged, max(len(merged), prev.get("sourceCount", 1), it["sourceCount"])
        else:
            it["firstSeen"] = iso(now)
        by_id[it["id"]] = it
    for mid in absorbed:
        by_id.pop(mid, None)
    cos = {c["ticker"]: c for c in cfg["companies"]}
    for it in list(by_id.values()):
        co = cos.get(it["ticker"])
        if not co:
            continue
        sc, drivers, noise = score_item(it, co, cfg)
        n = it.get("sourceCount", 1)
        sc += (n >= 3) + (n >= 6)
        if (noise or sc < 0) and not it.get("form"):
            del by_id[it["id"]]
            continue
        it["score"], it["drivers"], it["materiality"] = sc, drivers, level(sc, cfg)
    cutoff = iso(now - timedelta(days=cfg["keep_days"]))
    items = sorted((i for i in by_id.values() if i["publishedAt"] >= cutoff), key=lambda x: x["publishedAt"], reverse=True)[: cfg["max_items"]]
    meta = {"title": cfg["site_title"], "companies": [{"ticker": c["ticker"], "name": c["name"], "model": c.get("model")} for c in cfg["companies"]],
            "drivers": [{"id": d["id"], "label": d["label"], "assumption": d["assumption"], "weight": d["weight"]} for d in cfg["drivers"]]}
    return {"meta": meta, "items": items}, status


def main():
    cfg = json.loads((ROOT / "config.json").read_text())
    news, status = build(cfg)
    DATA.mkdir(parents=True, exist_ok=True)
    ok = [s for s in status["sources"] if s["ok"]]
    if not ok and (DATA / "news.json").exists():
        print("All sources failed, keeping previous data", file=sys.stderr)
        news = load(DATA / "news.json", news)
    (DATA / "news.json").write_text(json.dumps(news, ensure_ascii=False, separators=(",", ":")))
    (DATA / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1))
    print("items: %d, sources ok: %d/%d" % (len(news["items"]), len(ok), len(status["sources"])))
    for s in status["sources"]:
        if not s["ok"]:
            print("FAILED", s["name"], s["error"], file=sys.stderr)


if __name__ == "__main__":
    main()
