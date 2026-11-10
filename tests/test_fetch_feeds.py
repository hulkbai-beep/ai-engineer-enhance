"""Run: python3 -m unittest discover tests (no network: fetch is replaced by canned responses)."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_feeds as ff  # noqa: E402

RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>New &amp; long</title><link>https://blog.x/new</link><pubDate>Fri, 06 Nov 2026 10:00:00 GMT</pubDate>
  <description>short blurb</description><content:encoded><![CDATA[<p>{body}</p>]]></content:encoded></item>
<item><title>Known</title><guid>https://blog.x/known</guid><pubDate>Thu, 05 Nov 2026 10:00:00 GMT</pubDate>
  <description>seen before</description></item>
<item><title>Too old</title><link>https://blog.x/old</link><pubDate>Mon, 26 Oct 2026 10:00:00 GMT</pubDate></item>
</channel></rss>""".replace("{body}", "word " * 400)

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title type="html">Atom &lt;b&gt;post&lt;/b&gt;</title><link rel="alternate" href="https://atom.x/p"/>
  <link rel="replies" href="https://atom.x/p#c"/><published>2026-11-07T23:30:00-08:00</published>
  <content type="html">&lt;p&gt;hello&lt;/p&gt;</content></entry></feed>"""

SUBSTACK = {"posts": [
    {"title": "Free", "canonical_url": "https://news.x/p/free", "post_date": "2026-11-04T16:00:00.000Z",
     "audience": "everyone", "slug": "free", "body_html": None, "description": "free blurb"},
    {"title": "Paid", "canonical_url": "https://news.x/p/paid", "post_date": "2026-11-05T16:00:00.000Z",
     "audience": "only_paid", "slug": "paid", "body_html": "<p>" + "preview " * 400 + "</p>", "description": "paid blurb"},
    {"title": "Repost", "canonical_url": "https://other.x/p/r", "post_date": "2026-11-06T16:00:00.000Z",
     "audience": "everyone", "slug": "r", "body_html": "<p>x</p>", "description": "elsewhere"}]}


def paper(pid, upvotes, title):
    return {"paper": {"id": pid, "title": title, "upvotes": upvotes, "summary": "An  abstract.", "ai_summary": "One line."}}


class FetchFeedsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        ff.ROOT = self.tmp
        (self.tmp / "data").mkdir()
        (self.tmp / "data" / "items.json").write_text(json.dumps([{"url": "https://blog.x/known/"}]))
        (self.tmp / "data" / "state.json").write_text(json.dumps({"Blog": "2026-11-02", "HF Daily Papers": "2026-11-05"}))
        (self.tmp / "sources.json").write_text(json.dumps([
            {"name": "Blog", "url": "https://blog.x/rss", "feed": "xml"},
            {"name": "Newsletter", "url": "https://news.x/api/v1/archive?sort=new&limit=20", "feed": "substack"},
            {"name": "HF Daily Papers", "url": "https://huggingface.co/api/daily_papers?date=YYYY-MM-DD", "feed": "hf"},
            {"name": "Undated", "url": "https://undated.x/rss", "feed": "xml"},
            {"name": "Broken", "url": "https://broken.x/rss", "feed": "xml"},
            {"name": "Web page", "url": "https://page.x/blog"}]))
        undated = "".join(f"<item><title>U{n}</title><link>https://undated.x/{n}</link></item>" for n in range(4))
        self.pages = {
            "https://blog.x/rss": RSS,
            "https://news.x/api/v1/archive?sort=new&limit=20": json.dumps(SUBSTACK),
            "https://news.x/api/v1/posts/free": json.dumps({"body_html": "<p>" + "full " * 400 + "</p>"}),
            "https://huggingface.co/api/daily_papers?date=2026-11-04": json.dumps([paper("1", 3, "Low"), paper("2", 9, "Top")]),
            "https://huggingface.co/api/daily_papers?date=2026-11-05": "[]",
            "https://huggingface.co/api/daily_papers?date=2026-11-06": json.dumps([paper("3", 1, "Fri")]),
            "https://huggingface.co/api/daily_papers?date=2026-11-07": "[]",
            "https://huggingface.co/api/daily_papers?date=2026-11-08": "[]",
            "https://undated.x/rss": f'<rss version="2.0"><channel>{undated}</channel></rss>',
            "https://undated.x/0": '<script type="application/ld+json">{"datePublished": "2026-11-08"}</script>',
            "https://undated.x/1": '<meta property="article:published_time" content="2026-11-03T08:00:00Z">',
            "https://undated.x/2": '<time datetime="2026-10-20">Oct 20</time>',
        }
        self.fetched = []

        def fake_fetch(url):
            self.fetched.append(url)
            if url not in self.pages:
                raise OSError(f"Tunnel connection failed: 403 for {url}")
            return self.pages[url].encode()
        ff.fetch = fake_fetch

    def run_main(self, *argv):
        sys.argv = ["fetch_feeds.py", "--date", "2026-11-09", *argv]
        ff.main()
        return (self.tmp / ".feeds" / "feeds.md").read_text()

    def test_parses_rss_and_atom(self):
        rss = ff.parse_xml(RSS.encode())
        self.assertEqual([e["title"] for e in rss], ["New & long", "Known", "Too old"])
        self.assertEqual(rss[0]["date"], dt.date(2026, 11, 6))
        self.assertGreater(len(rss[0]["text"]), ff.FULL_TEXT_MIN)  # content:encoded wins over the blurb
        self.assertEqual(rss[1]["url"], "https://blog.x/known")  # no <link>: the guid
        atom = ff.parse_xml(ATOM.encode())
        self.assertEqual(atom, [{"title": "Atom post", "url": "https://atom.x/p", "date": dt.date(2026, 11, 7),
                                 "text": "hello"}])

    def test_parses_substack_and_hf(self):
        posts = ff.parse_substack(json.dumps(SUBSTACK).encode())
        self.assertEqual(posts[1]["text"], "paid blurb")  # a paid post's preview is not the article
        self.assertEqual(ff.parse_substack(json.dumps(SUBSTACK["posts"]).encode())[0]["title"], "Free")
        papers = ff.parse_hf(json.dumps([paper("1", 3, "Low"), paper("2", 9, " Top  ")]).encode())
        self.assertEqual([(p["title"], p["url"]) for p in papers],
                         [("Top", "https://huggingface.co/papers/2"), ("Low", "https://huggingface.co/papers/1")])
        self.assertEqual(papers[0]["abstract"], "An abstract.")

    def test_window_and_page_dates(self):
        d = dt.date(2026, 11, 9)
        self.assertEqual(ff.window_start("A", {"A": "2026-11-02"}, d), dt.date(2026, 11, 1))
        self.assertEqual(ff.window_start("New", {}, d), dt.date(2026, 11, 2))
        self.assertEqual(ff.page_date('<meta content="2026-11-03T08:00:00Z" property="article:published_time">'),
                         dt.date(2026, 11, 3))
        self.assertIsNone(ff.page_date("<p>no date here</p>"))

    def test_lists_window_entries_and_reports_failures(self):
        md = self.run_main()
        blog = md.split("## Blog")[1].split("\n## ")[0]
        self.assertIn("（窗口 2026-11-01 起，2 条）", blog)
        self.assertNotIn("Too old", blog)
        self.assertRegex(blog, r"\[Known\]\(https://blog.x/known\) ・已收录")
        long_entry = next(line for line in blog.splitlines() if "New & long" in line)
        text_file = self.tmp / long_entry.split("・正文 ")[1].split("（")[0]
        self.assertTrue(text_file.read_text().startswith("New & long\nhttps://blog.x/new\n2026-11-06"))
        news = md.split("## Newsletter")[1].split("\n## ")[0]
        self.assertIn("（窗口 2026-11-02 起，3 条）", news)  # never read: D-7
        self.assertIn("・正文 ", next(line for line in news.splitlines() if "[Free]" in line))  # from the post API
        self.assertIn("・付费文 ・简介：paid blurb", news)
        self.assertIn("[Repost](https://other.x/p/r) ・转载（别的域名）", news)
        self.assertNotIn("https://news.x/api/v1/posts/paid", self.fetched)
        self.assertIn("## 读取失败", md)
        self.assertIn("- Broken：OSError", md)
        self.assertNotIn("Web page", md)

    def test_hf_lists_each_day_but_today_by_upvotes(self):
        hf = self.run_main().split("## HF Daily Papers")[1].split("\n## ")[0]
        self.assertIn("（窗口 2026-11-04 起，不含 2026-11-09", hf)
        self.assertNotIn("date=2026-11-09", " ".join(self.fetched))
        self.assertLess(hf.index("9 赞 [Top]"), hf.index("3 赞 [Low]"))
        self.assertIn("### 2026-11-06（1 篇）", hf)
        self.assertNotIn("### 2026-11-05", hf)  # an empty day adds nothing
        self.assertTrue((self.tmp / hf.split("・摘要 ")[1].split(" ")[0]).read_text().endswith("An abstract.\n"))

    def test_undated_feed_reads_dates_from_pages_until_one_is_too_old(self):
        undated = self.run_main().split("## Undated")[1].split("\n## ")[0]
        self.assertIn("第 3 篇已早于窗口", undated)
        self.assertIn("- 2026-11-08 [U0]", undated)
        self.assertNotIn("https://undated.x/3", self.fetched)

    def test_rerun_clears_old_texts_and_rejects_unknown_names(self):
        self.run_main()
        stale = self.tmp / ".feeds" / "text" / "stale.txt"
        stale.write_text("from last week")
        self.run_main("--only", "Blog")
        self.assertFalse(stale.exists())
        with self.assertRaises(SystemExit):
            self.run_main("--only", "Web page")


if __name__ == "__main__":
    unittest.main()
