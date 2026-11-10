"""Read the weekly run's machine-readable sources from the shell, so they cost no WebFetch calls:
RSS/Atom feeds, Substack archives and the Hugging Face daily-papers API. Only sources with a
"feed" key in sources.json ("xml", "substack" or "hf") are read; the others stay with WebFetch.

Usage: python3 scripts/fetch_feeds.py --date D [--only "source name" ...] [--out DIR]

Writes DIR/feeds.md (default .feeds/, which git ignores): each source's window, worked out like
skill step 1 from data/state.json, and the entries in it with date, title and link, marked when
data/items.json already has the link. Full article texts and paper abstracts go to DIR/text/, and
feeds.md names the file. Sources that fail are listed at the end with the error, so the run can
try them with WebFetch. The shell goes through the same network allow-list as WebFetch.
Reads data/ and sources.json; writes only under DIR.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import re
import shutil
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "ai-engineer-enhance weekly digest (feed reader)"
FULL_TEXT_MIN = 1500  # a feed body shorter than this is a blurb, not the article
MAX_DATE_PAGES = 25  # undated feeds: article pages opened per run to find dates
HF_PER_DAY = 20  # papers listed per day; the five picked each day ranked 11th or higher in Nov 2026
ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
DC_DATE = "{http://purl.org/dc/elements/1.1/}date"
PAGE_DATE = [r'"datePublished"\s*:\s*"([^"]+)"',
             r'<meta[^>]+property="article:published_time"[^>]+content="([^"]+)"',
             r'<meta[^>]+content="([^"]+)"[^>]+property="article:published_time"',
             r'<time[^>]+datetime="([^"]+)"']


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return body


def plain(fragment):
    """HTML fragment -> readable plain text."""
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment or "")
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|blockquote|pre|tr)>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\s*\n\s*(\n\s*)*", lambda m: "\n\n" if m.group(1) else "\n", s).strip()


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError, IndexError):
        return None


def page_date(page):
    """Publish date of an article page, from JSON-LD, article:published_time or a <time> tag."""
    for pattern in PAGE_DATE:
        m = re.search(pattern, page)
        if m and parse_date(m.group(1)):
            return parse_date(m.group(1))
    return None


def norm_url(url):
    return (url or "").strip().rstrip("/")


def host(url):
    return urllib.parse.urlparse(url).netloc.lower()


def parse_xml(body):
    """RSS 2.0 or Atom -> [{title, url, date, text}]; text is the longest body the feed gives."""
    root = ET.fromstring(body)
    out = []
    for it in root.iter("item"):
        bodies = [it.findtext(CONTENT) or "", it.findtext("description") or ""]
        out.append({"title": plain(it.findtext("title")),
                    "url": (it.findtext("link") or it.findtext("guid") or "").strip(),
                    "date": parse_date(it.findtext("pubDate") or it.findtext(DC_DATE)),
                    "text": max((plain(b) for b in bodies), key=len)})
    for e in root.iter(ATOM + "entry"):
        links = e.findall(ATOM + "link")
        link = next((l.get("href") for l in links if l.get("rel", "alternate") == "alternate"), None)
        bodies = [e.findtext(ATOM + "content") or "", e.findtext(ATOM + "summary") or ""]
        out.append({"title": plain(e.findtext(ATOM + "title")), "url": (link or "").strip(),
                    "date": parse_date(e.findtext(ATOM + "published") or e.findtext(ATOM + "updated")),
                    "text": max((plain(b) for b in bodies), key=len)})
    return out


def parse_substack(body):
    """Substack /api/v1/archive -> entries; paid posts keep only their blurb."""
    data = json.loads(body)
    posts = data["posts"] if isinstance(data, dict) else data
    out = []
    for p in posts:
        blurb = p.get("description") or p.get("subtitle") or p.get("truncated_body_text") or ""
        free = p.get("audience") == "everyone"
        out.append({"title": (p.get("title") or "").strip(), "url": p.get("canonical_url") or "",
                    "date": parse_date(p.get("post_date")), "audience": p.get("audience"),
                    "slug": p.get("slug"), "text": (plain(p.get("body_html")) if free else "") or blurb})
    return out


def parse_hf(body):
    """HF /api/daily_papers?date= -> papers by upvotes, with the abstract."""
    out = []
    for x in json.loads(body):
        p = x.get("paper") or {}
        out.append({"id": p.get("id"), "title": " ".join((p.get("title") or x.get("title") or "").split()),
                    "upvotes": p.get("upvotes") or 0, "ai_summary": " ".join((p.get("ai_summary") or "").split()),
                    "abstract": " ".join((p.get("summary") or x.get("summary") or "").split()),
                    "url": f"https://huggingface.co/papers/{p.get('id')}"})
    return sorted(out, key=lambda p: -p["upvotes"])


def window_start(name, state, date):
    """First date in the window (skill step 1): two days of overlap with the last read, D-7 if never read."""
    last = state.get(name)
    return dt.date.fromisoformat(last) - dt.timedelta(days=1) if last else date - dt.timedelta(days=7)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Writer:
    """Saves texts under out/text and tells feeds.md where they are."""

    def __init__(self, out, items):
        self.out, self.text = out, out / "text"
        self.known = {norm_url(i["url"]) for i in items}

    def save(self, name, url, header, text):
        key = hashlib.sha1(url.encode()).hexdigest()[:6]
        path = self.text / f"{slug(name)}--{slug(urllib.parse.urlparse(url).path)[-50:]}-{key}.txt"
        path.write_text(f"{header}\n\n{text}\n")
        return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path

    def entry(self, source, e):
        flags = ["已收录"] if norm_url(e["url"]) in self.known else []
        if source["feed"] == "substack" and host(e["url"]) != host(source["url"]):
            flags.append("转载（别的域名）")
        if e.get("audience") not in (None, "everyone"):
            flags.append("付费文")
        if len(e["text"]) >= FULL_TEXT_MIN:
            path = self.save(source["name"], e["url"], f"{e['title']}\n{e['url']}\n{e['date']}", e["text"])
            flags.append(f"正文 {path}（{len(e['text'])} 字符）")
        elif e["text"]:
            flags.append("简介：" + " ".join(e["text"].split())[:300])
        return f"- {e['date'] or '无日期'} [{e['title']}]({e['url']})" + "".join(f" ・{f}" for f in flags)


def date_from_pages(entries, start):
    """Undated feeds: open article pages in feed order (newest first) until one predates the window.
    Returns the entries looked at (the rest are older) and a note for feeds.md."""
    for n, e in enumerate(entries[:MAX_DATE_PAGES], 1):
        try:
            e["date"] = page_date(fetch(e["url"]).decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 -- that entry stays undated and is listed for the run to check
            e["date"] = None
        if e["date"] and e["date"] < start:
            return entries[:n], f"_feed 没有日期：从文章页读了前 {n} 篇的日期，第 {n} 篇已早于窗口，后面的没再看。_"
    looked = entries[:MAX_DATE_PAGES]
    return looked, f"_feed 没有日期：从文章页读了前 {len(looked)} 篇的日期，还没到窗口起点，更早的要到列表页看。_"


def read_feed(source, start, date, w):
    """Lines for one RSS/Atom/Substack source."""
    body = fetch(source["url"])
    entries = parse_substack(body) if source["feed"] == "substack" else parse_xml(body)
    page_note = None
    if entries and not any(e["date"] for e in entries):
        entries, page_note = date_from_pages(entries, start)
    for e in entries:
        if (e.get("audience") == "everyone" and len(e["text"]) < FULL_TEXT_MIN and e["slug"]
                and host(e["url"]) == host(source["url"]) and e["date"] and start <= e["date"] <= date):
            site = "https://" + host(source["url"])  # free post whose list entry had no body
            try:
                e["text"] = plain(json.loads(fetch(f"{site}/api/v1/posts/{e['slug']}")).get("body_html")) or e["text"]
            except Exception:  # noqa: BLE001 -- keep the blurb; the run can open the post with WebFetch
                pass
    dated = [e for e in entries if e["date"]]
    in_window = sorted((e for e in dated if start <= e["date"] <= date), key=lambda e: e["date"])
    lines = [f"## {source['name']}（窗口 {start} 起，{len(in_window)} 条）", ""]
    if page_note:
        lines += [page_note, ""]
    elif dated and min(e["date"] for e in dated) > start:
        lines += [f"_feed 最早一条是 {min(e['date'] for e in dated)}，晚于窗口起点：更早的条目要到列表页看。_", ""]
    lines += [w.entry(source, e) for e in in_window]
    undated = [e for e in entries if not e["date"]]
    if undated:
        lines += ["", f"另有 {len(undated)} 条没读到日期，按来源的 note 用 WebFetch 看："]
        lines += [w.entry(source, e) for e in undated]
    return lines + [""]


def read_hf(source, start, date, w):
    """Lines for HF Daily Papers: every list date in the window except D, papers by upvotes."""
    lines = [f"## {source['name']}（窗口 {start} 起，不含 {date}；每天按点赞数）", ""]
    # Today's list (UTC) is still filling up and the API answers 400 until it starts; the overlap of
    # the next run's window picks that day up.
    day, end = start, min(date, dt.datetime.now(dt.timezone.utc).date())
    while day < end:
        papers = parse_hf(fetch(f"https://huggingface.co/api/daily_papers?date={day}"))
        if papers:
            top = f"，只列点赞前 {HF_PER_DAY}" if len(papers) > HF_PER_DAY else ""
            lines += [f"### {day}（{len(papers)} 篇{top}）", ""]
        for p in papers[:HF_PER_DAY]:
            path = w.save("hf", p["url"], f"{p['title']}\n{p['url']}\n列表日期 {day}，点赞 {p['upvotes']}", p["abstract"])
            done = " ・已收录" if norm_url(p["url"]) in w.known else ""
            note = f" ・{p['ai_summary'][:160]}" if p["ai_summary"] else ""
            lines.append(f"- {p['upvotes']} 赞 [{p['title']}]({p['url']}){done} ・摘要 {path}{note}")
        if papers:
            lines.append("")
        day += dt.timedelta(days=1)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="run date D (default: today)")
    ap.add_argument("--only", action="append", default=[], help="read just this source (repeatable)")
    ap.add_argument("--out", default=str(ROOT / ".feeds"), help="where feeds.md and text/ go (default .feeds/)")
    args = ap.parse_args()
    date = dt.date.fromisoformat(args.date)
    sources = [s for s in json.loads((ROOT / "sources.json").read_text()) if s.get("feed")]
    unknown = sorted(set(args.only) - {s["name"] for s in sources})
    if unknown:
        raise SystemExit(f"--only {unknown}: not a source with a feed key in sources.json")
    sources = [s for s in sources if not args.only or s["name"] in args.only]
    state_file, items_file = ROOT / "data" / "state.json", ROOT / "data" / "items.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    w = Writer(Path(args.out), json.loads(items_file.read_text()) if items_file.exists() else [])
    shutil.rmtree(w.text, ignore_errors=True)  # texts from an earlier run would look like this run's
    w.text.mkdir(parents=True)
    lines = [f"# 脚本读取的来源 {date}", "",
             "已收录 = items.json 里已有这个链接；正文 / 摘要 = 全文存在那个文件里，读它就算读过原文；"
             "简介 = feed 只给了这些，要原文得用 WebFetch 打开链接。", ""]
    failed = []
    for s in sources:
        try:
            reader = read_hf if s["feed"] == "hf" else read_feed
            lines += reader(s, window_start(s["name"], state, date), date, w)
        except Exception as e:  # noqa: BLE001 -- report and go on; the run reads this source with WebFetch
            failed.append(f"- {s['name']}：{type(e).__name__}: {e}")
    if failed:
        lines += ["## 读取失败（改用 WebFetch 读，还读不到就用 --unread 记为未读取）", ""] + failed + [""]
    (w.out / "feeds.md").write_text("\n".join(lines))
    print(f"{len(sources) - len(failed)} of {len(sources)} sources read; see {w.out / 'feeds.md'}")


if __name__ == "__main__":
    main()
