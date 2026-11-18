import json, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch as F

cfg = json.loads((ROOT / "config.json").read_text())
co = cfg["companies"][0]
NOW = datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)

def item(t, src, d="Tue, 17 Nov 2026 15:00:00 GMT"):
    return "<item><title>%s - %s</title><link>https://example.com/%d</link><pubDate>%s</pubDate><source url='x'>%s</source><description>&lt;a href='x'&gt;%s&lt;/a&gt;</description></item>" % (t, src, abs(hash(t + src)), d, src, t)

RSS = "<rss><channel>" + "".join([
    item("Anthropic IPO filing shows $47.2 billion compute commitment to Microsoft Azure", "Reuters"),
    item("Anthropic IPO filing shows $47.2 billion compute commitment to Microsoft Azure cloud", "CNBC"),
    item("Microsoft unveils Maia 300 chip and Copilot Credits at Ignite", "The Verge"),
    item("Microsoft 365 outage hits users worldwide for a day", "BleepingComputer"),
    item("JPMorgan reiterates Overweight on Microsoft with $625 price target", "Investing.com"),
    item("3 AI stocks to buy now: Microsoft vs. Nvidia", "The Motley Fool"),
    item("How to install the new Microsoft Teams update", "Windows Central"),
    item("Old Microsoft story", "Reuters", "Tue, 03 Nov 2026 15:00:00 GMT"),
]) + "</channel></rss>"

def fake_fetch(url):
    if "news.google.com" in url:
        return RSS.encode()
    raise OSError("offline in test")

F.DATA = ROOT / "tests" / "_tmp"
news, status = F.build(cfg, fetch=fake_fetch, now=NOW)
by = {i["title"]: i for i in news["items"]}
for i in news["items"]:
    print("%-6s %4.1f %-2d %s %s" % (i["materiality"], i["score"], i["sourceCount"], i["title"][:70], i["drivers"]))

a = [i for i in news["items"] if i["title"].startswith("Anthropic IPO")]
assert len(a) == 1 and a[0]["sourceCount"] == 2, "near-duplicate headlines must merge"
assert a[0]["materiality"] == "high" and "azure" in a[0]["drivers"]
assert a[0]["title"].endswith("Azure") or a[0]["title"].endswith("cloud"), "publisher suffix must be stripped"
assert by["Microsoft unveils Maia 300 chip and Copilot Credits at Ignite"]["materiality"] in ("medium", "high")
assert by["Microsoft 365 outage hits users worldwide for a day"]["materiality"] != "high"
assert by["JPMorgan reiterates Overweight on Microsoft with $625 price target"]["materiality"] in ("low", "medium")
assert not any("stocks to buy" in t for t in by), "listicles must be dropped"
assert not any("Old Microsoft" in t for t in by), "stale items must be dropped"
assert any(not s["ok"] for s in status["sources"]) and any(s["ok"] for s in status["sources"])

p = F.price_summary([("2025-12-31", 480.0)] + [("2026-11-%02d" % d, 500.0 + d) for d in range(1, 19)], "test")
assert p["price"] == 518.0 and p["d1"] == round((518 / 517 - 1) * 100, 2) and p["ytd"] == round((518 / 480 - 1) * 100, 2)

sec = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><category term="8-K"/><link href="https://www.sec.gov/x"/><updated>2026-11-17T16:05:00-05:00</updated><summary>Items 2.02</summary><title>8-K</title></entry><entry><category term="4"/><link href="https://www.sec.gov/y"/><updated>2026-11-17T16:05:00-05:00</updated><title>4</title></entry></feed>"""
s = F.parse_sec(sec, co)
assert len(s) == 1 and s[0]["form"] == "8-K"
print("all tests passed")
