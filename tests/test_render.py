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
            "difficulty": "进阶", "relevance": 3, "full_text_read": True, "category": "agents"}
    return {**base, **kw}


def posting(company, *cats, n=0):
    return {"company": company, "title": "AI Engineer", "location": "San Francisco, CA | Remote (US)",
            "url": f"https://jobs/{company}/{n}", "posted": "2026-10-20", "via": "Greenhouse", "skills": [{"category": c, "phrase": f"{company}-{c}-{i}"} for i, c in enumerate(cats)]}


def snapshot(skills):
    return {"date": "x", "source": "skills.sh",
            "skills": [{"name": n, "repo": "r", "url": f"https://skills.sh/{n}", "installs": i, "desc_zh": "d"}
                       for n, i in skills]}


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        render.ROOT, render.ITEMS = self.tmp, self.tmp / "data" / "items.json"
        render.SKILLS, render.DIGESTS = self.tmp / "data" / "skills", self.tmp / "digests"
        render.JOBS = self.tmp / "data" / "jobs"
        render.STATE, render.SOURCES = self.tmp / "data" / "state.json", self.tmp / "sources.json"
        render.SKILLS.mkdir(parents=True)
        render.SOURCES.write_text(json.dumps([{"name": "OpenAI"}, {"name": "Meta AI"}]))

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
        with self.assertRaises(ValueError):
            render.validate(item("https://a.com", category="rag"))
        with self.assertRaises(ValueError):
            render.validate(item("https://a.com", category=["agents"]))  # a list, as tags are
        no_category = item("https://a.com", relevance=0)
        del no_category["category"]
        render.validate(no_category)  # irrelevant items may skip the category

    def test_readme_groups_by_category_in_fixed_order(self):
        self.run_main([item("https://a.com", category="evals", title="E"),
                       item("https://b.com", category="agents", title="A1"),
                       item("https://c.com", category="agents", title="A2")], "--date", "2026-10-29")
        readme = (self.tmp / "README.md").read_text()
        self.assertLess(readme.index("### Agent 架构与编排（2 条）"), readme.index("### 评测与可观测性（1 条）"))
        self.assertNotIn("提示词与结构化输出", readme)

    def test_readme_hides_irrelevant_and_marks_unread(self):
        self.run_main([item("https://a.com", relevance=0, title="Biz"),
                       item("https://b.com", full_text_read=False, title="Agent|Post", tags=["a|b"])],
                      "--date", "2026-10-29")
        readme = (self.tmp / "README.md").read_text()
        self.assertNotIn("Biz", readme)
        self.assertIn("Agent\\|Post", readme)
        self.assertIn("| a\\|b |", readme)
        self.assertIn("未读到正文", readme)

    def test_digest_only_this_week_with_must_read_and_unread_sources(self):
        self.run_main([item("https://a.com", relevance=5, title="Hot"),
                       item("https://c.com", relevance=4, title="Blurb", full_text_read=False),
                       item("https://old.com", added="2026-10-22", title="Old")],
                      "--date", "2026-10-29", "--unread", "Meta AI")
        digest = (render.DIGESTS / "2026-10-29.md").read_text()
        self.assertIn("本周必读", digest)
        self.assertIn("Hot", digest)
        self.assertIn("- [ ] **[Blurb](https://c.com)**（OpenAI）：摘要（未读到正文）", digest)
        self.assertNotIn("Old", digest)
        self.assertIn("来源未读取：Meta AI", digest)

    def test_digest_lists_only_items_scoring_3_or_more(self):
        self.run_main([item("https://a.com", relevance=3, title="Keep"),
                       item("https://b.com", relevance=2, title="Minor", full_text_read=False),
                       item("https://c.com", relevance=0, title="Biz")], "--date", "2026-10-29")
        digest = (render.DIGESTS / "2026-10-29.md").read_text()
        self.assertIn("本周新增 2 条。下面只列 3 分以上的 1 条，其余 1 条在 [README 总表]", digest)
        self.assertIn("Keep", digest)
        self.assertNotIn("Minor", digest)  # not in the tables, nor under 没读到的内容
        self.assertNotIn("没读到的内容", digest)
        self.assertIn("Minor", (self.tmp / "README.md").read_text())
        self.run_main([item("https://b.com", relevance=2, title="Minor")], "--date", "2026-10-29")
        self.assertIn("_没有 3 分以上的新条目。_", (render.DIGESTS / "2026-10-29.md").read_text())

    def test_state_records_each_source_last_read(self):
        self.run_main([], "--date", "2026-10-29", "--unread", "Meta AI")
        state = json.loads(render.STATE.read_text())
        # A source never read yet gets D-7, and keeps it while it stays unread, so no week is skipped.
        self.assertEqual(state, {"OpenAI": "2026-10-29", "Meta AI": "2026-10-22", "skills.sh": "2026-10-29"})
        self.run_main([], "--date", "2026-11-02", "--unread", "Meta AI")
        self.assertEqual(json.loads(render.STATE.read_text())["Meta AI"], "2026-10-22")
        self.run_main([], "--date", "2026-11-09")
        self.assertEqual(json.loads(render.STATE.read_text())["Meta AI"], "2026-11-09")
        self.run_main([], "--date", "2026-10-29")  # re-rendering an old week never moves a source back
        self.assertEqual(json.loads(render.STATE.read_text())["OpenAI"], "2026-11-09")

    def test_no_state_rerender_leaves_state_alone(self):
        self.run_main([], "--date", "2026-10-29")
        before = render.STATE.read_text()
        render.SOURCES.write_text(json.dumps([{"name": "OpenAI"}, {"name": "Meta AI"}, {"name": "New"}]))
        self.run_main([], "--date", "2026-10-29", "--no-state")  # "New" was never read
        self.assertEqual(render.STATE.read_text(), before)

    def test_rejects_unread_name_not_in_sources(self):
        with self.assertRaises(ValueError):
            self.run_main([], "--date", "2026-10-29", "--unread", "Meta")
        self.assertFalse(render.STATE.exists())

    def test_same_day_rerun_does_not_list_a_source_read_earlier_today(self):
        self.run_main([], "--date", "2026-10-29", "--unread", "Meta AI")
        self.run_main([], "--date", "2026-10-29", "--unread", "OpenAI", "--unread", "Meta AI")
        digest = (render.DIGESTS / "2026-10-29.md").read_text()
        self.assertNotIn("来源未读取：OpenAI", digest)
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

    def test_job_report_counts_postings_and_flags_gaps(self):
        render.JOBS.mkdir(parents=True)
        (render.JOBS / "2026-11.json").write_text(json.dumps(
            {"date": "2026-11-01", "postings": [posting("A", "evals", "agents", "evals", "career"),
                                                posting("B", "evals", "career"), posting("C", "agents", "career")]}))
        articles = [item(f"https://x.com/{n}", category="agents", relevance=4) for n in range(3)]
        articles.append(item("https://y.com", category="evals", relevance=3))  # below must-read grade
        articles.append(item("https://z.com", category="evals", relevance=5, added="2026-09-29"))  # not October
        self.run_main(articles, "--date", "2026-11-02")
        report = (render.DIGESTS / "jobs-2026-11.md").read_text()
        self.assertIn("| 评测与可观测性 | 2/3 | A-evals-0；B-evals-0 | 0 |", report)  # one phrase per company
        self.assertIn("| Agent 架构与编排 | 2/3 | A-agents-1；C-agents-0 | 3 |", report)
        self.assertIn("| 职业与行业动态 | 3/3 | A-career-3；B-career-1；C-career-1 | 不参与对比 |", report)
        # Agents has 3 must-read October articles and career is never compared, so only evals is a gap.
        self.assertIn("2026-10 收的 4 分以上文章不到 3 篇：评测与可观测性（2/3 份职位，0 篇文章）。", report)
        self.assertIn("| A | [AI Engineer](https://jobs/A/0) | San Francisco, CA \\| Remote (US) | 2026-10-20 | Greenhouse |",
                      report)
        digest = (render.DIGESTS / "2026-11-02.md").read_text()
        self.assertIn("## 本月岗位技能", digest)
        self.assertIn("digests/jobs-2026-11.md", digest)
        self.assertIn("- [2026-11](digests/jobs-2026-11.md)", (self.tmp / "README.md").read_text())
        self.run_main(articles, "--date", "2026-11-09")  # later digests of the month leave it out
        self.assertNotIn("岗位技能", (render.DIGESTS / "2026-11-09.md").read_text())

    def test_rejects_job_snapshots_the_report_would_get_wrong(self):
        render.JOBS.mkdir(parents=True)
        no_date, no_place = posting("A", "agents"), posting("A", "agents")
        del no_date["posted"], no_place["location"]
        for snap, message in [({"date": "2026-11-01", "postings": []}, "no postings"),
                              ({"date": "2026-10-31", "postings": [posting("A", "agents")]}, "is not in 2026-11"),
                              ({"date": "2026-11-01", "postings": [no_date]}, r"missing \['posted'\]"),
                              ({"date": "2026-11-01", "postings": [no_place]}, r"missing \['location'\]"),
                              ({"date": "2026-11-01", "postings": [posting("A", "rag")]}, "each skill needs"),
                              ({"date": "2026-11-01", "postings": [posting("A")]}, "each skill needs"),
                              ({"date": "2026-11-01", "postings": [{**posting("A"), "skills": [{"category": "agents"}]}]},
                               "each skill needs"),
                              ({"date": "2026-11-01", "postings": [
                                  {**posting("A"), "skills": [{"category": ["agents"], "phrase": "p"}]}]},
                               "each skill needs")]:
            (render.JOBS / "2026-11.json").write_text(json.dumps(snap))
            with self.assertRaisesRegex(ValueError, message):
                self.run_main([], "--date", "2026-11-02")

    def test_job_snapshots_cap_each_company_and_never_repeat_a_posting(self):
        render.JOBS.mkdir(parents=True)
        nov = render.JOBS / "2026-11.json"
        nov.write_text(json.dumps({"date": "2026-11-01", "postings": [posting("A", "agents", n=n) for n in range(4)]}))
        with self.assertRaisesRegex(ValueError, r"more than 3 postings from \['A'\]"):
            self.run_main([], "--date", "2026-11-02")
        (render.JOBS / "2026-10.json").write_text(json.dumps({"date": "2026-10-05", "postings": [posting("A", "agents")]}))
        nov.write_text(json.dumps({"date": "2026-11-01", "postings": [posting("A", "agents")]}))
        with self.assertRaisesRegex(ValueError, "already in 2026-10.json"):
            self.run_main([], "--date", "2026-11-02")

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
