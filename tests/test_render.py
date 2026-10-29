"""Run: python3 -m unittest discover tests"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import render  # noqa: E402


def item(url, **kw):
    base = {"date": "2026-10-28", "added": "2026-10-29", "source": "OpenAI", "type": "blog",
            "title": "T", "url": url, "summary_zh": "摘要", "tags": ["agent"],
            "difficulty": "进阶", "relevance": 3, "full_text_read": True}
    return {**base, **kw}


def snapshot(skills):
    return {"date": "x", "source": "skills.sh",
            "skills": [{"name": n, "repo": "r", "url": f"https://skills.sh/{n}", "installs": i, "desc_zh": "d"}
                       for n, i in skills]}


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        render.ROOT, render.ITEMS = self.tmp, self.tmp / "data" / "items.json"
        render.SKILLS, render.DIGESTS = self.tmp / "data" / "skills", self.tmp / "digests"
        render.SKILLS.mkdir(parents=True)

    def run_main(self, items, *argv):
        render.ITEMS.write_text(json.dumps(items))
        sys.argv = ["render.py", *argv]
        render.main()
        return json.loads(render.ITEMS.read_text())

    def test_dedupes_by_url_ignoring_trailing_slash(self):
        out = self.run_main([item("https://a.com/x"), item("https://a.com/x/"), item("https://b.com")],
                            "--date", "2026-10-29")
        self.assertEqual([i["url"] for i in out], ["https://a.com/x", "https://b.com"])

    def test_keeps_different_entries_that_share_a_docs_url(self):
        out = self.run_main([item("https://docs.x/cli", title="CLI beta"), item("https://docs.x/cli", title="CLI GA")],
                            "--date", "2026-10-29")
        self.assertEqual([i["title"] for i in out], ["CLI beta", "CLI GA"])

    def test_rejects_missing_field_and_bad_relevance(self):
        bad = item("https://a.com")
        del bad["summary_zh"]
        with self.assertRaises(ValueError):
            render.validate(bad)
        with self.assertRaises(ValueError):
            render.validate(item("https://a.com", relevance=9))

    def test_readme_hides_irrelevant_and_marks_unread(self):
        self.run_main([item("https://a.com", relevance=0, title="Biz"),
                       item("https://b.com", full_text_read=False, title="Agent|Post")], "--date", "2026-10-29")
        readme = (self.tmp / "README.md").read_text()
        self.assertNotIn("Biz", readme)
        self.assertIn("Agent\\|Post", readme)
        self.assertIn("未读到正文", readme)

    def test_digest_only_this_week_with_must_read_and_unread_sources(self):
        self.run_main([item("https://a.com", relevance=5, title="Hot"),
                       item("https://c.com", relevance=4, title="Blurb", full_text_read=False),
                       item("https://old.com", added="2026-10-22", title="Old")],
                      "--date", "2026-10-29", "--unread", "Meta AI")
        digest = (render.DIGESTS / "2026-10-29.md").read_text()
        self.assertIn("本周必读", digest)
        self.assertIn("Hot", digest)
        self.assertIn("- **[Blurb](https://c.com)**（OpenAI）：摘要（未读到正文）", digest)
        self.assertNotIn("Old", digest)
        self.assertIn("来源未读取：Meta AI", digest)

    def test_digest_names_the_snapshot_it_compares_against(self):
        (render.SKILLS / "2026-10-22.json").write_text(json.dumps(snapshot([("a", 100)])))
        (render.SKILLS / "2026-10-29.json").write_text(json.dumps(snapshot([("a", 110)])))
        self.run_main([], "--date", "2026-10-29")
        digest = (render.DIGESTS / "2026-10-29.md").read_text()
        self.assertIn("### 新上榜（对比 2026-10-22）", digest)
        self.assertIn("### 增长最快（对比 2026-10-22）", digest)

    def test_skill_ranking_new_and_growth(self):
        (render.SKILLS / "2026-10-22.json").write_text(json.dumps(snapshot([("a", 100), ("b", 50)])))
        (render.SKILLS / "2026-10-29.json").write_text(json.dumps(snapshot([("a", 110), ("b", 90), ("c", 5)])))
        lines = render.skill_section(render.load_snapshot(render.SKILLS / "2026-10-29.json"),
                                     render.load_snapshot(render.SKILLS / "2026-10-22.json"), "榜")
        text = "\n".join(lines)
        new_part = text.split("### 新上榜")[1].split("### 增长最快")[0]
        self.assertIn("[c]", new_part)
        self.assertNotIn("[a]", new_part)
        growth = text.split("### 增长最快")[1]
        self.assertLess(growth.index("+40"), growth.index("+10"))

    def test_digest_shows_trending_skills(self):
        snap = snapshot([("a", 100)])
        snap["trending"] = snapshot([("hot-one", 20)])["skills"]
        (render.SKILLS / "2026-10-29.json").write_text(json.dumps(snap))
        self.run_main([], "--date", "2026-10-29")
        digest = (render.DIGESTS / "2026-10-29.md").read_text()
        self.assertIn("近期热门（skills.sh Trending 榜）", digest)
        self.assertIn("[hot-one]", digest)

    def test_first_snapshot_has_no_comparison(self):
        text = "\n".join(render.skill_section({"r/a": snapshot([("a", 1)])["skills"][0]}, None, "榜"))
        self.assertIn("还没有更早的快照", text)

    def test_monthly_ranking_written_on_last_run_of_month(self):
        (render.SKILLS / "2026-09-28.json").write_text(json.dumps(snapshot([("a", 10)])))
        (render.SKILLS / "2026-10-26.json").write_text(json.dumps(snapshot([("a", 30)])))
        self.run_main([], "--date", "2026-10-26")
        self.assertIn("+20", (render.DIGESTS / "skills-2026-10.md").read_text())
        (render.DIGESTS / "skills-2026-10.md").unlink()
        (render.SKILLS / "2026-10-19.json").write_text(json.dumps(snapshot([("a", 20)])))
        self.run_main([], "--date", "2026-10-19")  # not the last run of October
        self.assertFalse((render.DIGESTS / "skills-2026-10.md").exists())


if __name__ == "__main__":
    unittest.main()
