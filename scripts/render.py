"""Dedupe and validate data/items.json, then write README.md, the weekly digest, the monthly
job-skill reports (digests/jobs-YYYY-MM.md from data/jobs/) and data/state.json.

Usage: python3 scripts/render.py [--date YYYY-MM-DD] [--unread "source A" --unread "source B"] [--no-state]

data/state.json records the last date each source was read: every name in sources.json
(plus skills.sh) that is not passed with --unread. An --unread source that was never read gets the
date minus 7 days, so its window keeps starting there until a run reads it. Re-rendering outside
the weekly run (an older date, or after editing data/jobs/) takes --no-state, which leaves
data/state.json as it is.
Reads only files inside the repo; no network access.
"""
import argparse
import datetime as dt
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ITEMS = ROOT / "data" / "items.json"
STATE = ROOT / "data" / "state.json"
SOURCES = ROOT / "sources.json"
SKILLS = ROOT / "data" / "skills"
JOBS = ROOT / "data" / "jobs"
DIGESTS = ROOT / "digests"
REPO_URL = "https://github.com/hulkbai-beep/ai-engineer-enhance/blob/main"

REQUIRED = ["date", "added", "source", "type", "title", "url", "summary_zh",
            "tags", "difficulty", "relevance", "full_text_read"]
TYPES = {"paper", "blog", "docs", "release"}
DIFFICULTIES = {"入门", "进阶", "研究"}
# One shared set for articles (items.json "category") and job-posting skill phrases (data/jobs/*.json).
CATEGORIES = {
    "agents": "Agent 架构与编排",
    "tools-mcp": "工具、MCP 与 Skills",
    "context-rag": "上下文工程、检索与记忆",
    "prompting": "提示词与结构化输出",
    "evals": "评测与可观测性",
    "models": "模型发布与选型",
    "api-sdk": "API、SDK 与开发栈",
    "training": "微调与后训练",
    "infra": "推理服务与基础设施",
    "safety": "安全、隐私与治理",
    "applied": "落地案例与客户方案",
    "career": "职业与行业动态",
}
# In postings these mean languages and general dev, client delivery, years and communication:
# reading articles can't close them, so job reports list them but never flag them as gaps.
NOT_COMPARED = {"api-sdk", "applied", "career"}
MAX_PER_COMPANY = 3
DIGEST_MIN = 3  # the weekly digest (and its Issue) lists items from this score; README lists everything from 1


def norm_url(url):
    return url.strip().rstrip("/")


def validate(item):
    missing = [k for k in REQUIRED if k not in item]
    if missing:
        raise ValueError(f"{item.get('url', item)}: missing {missing}")
    for k in ("date", "added"):
        dt.date.fromisoformat(item[k])
    if item["type"] not in TYPES:
        raise ValueError(f"{item['url']}: type must be one of {sorted(TYPES)}")
    if item["difficulty"] not in DIFFICULTIES:
        raise ValueError(f"{item['url']}: difficulty must be one of {sorted(DIFFICULTIES)}")
    if not (isinstance(item["relevance"], int) and 0 <= item["relevance"] <= 5):
        raise ValueError(f"{item['url']}: relevance must be an int 0-5")
    # Relevance-0 items are never shown or compared, so they may leave the category out.
    category = item.get("category")
    if (item["relevance"] >= 1 or "category" in item) and not (isinstance(category, str) and category in CATEGORIES):
        raise ValueError(f"{item['url']}: category must be one of {list(CATEGORIES)}")


def dedupe(items):
    # Changelog entries often link the same docs page, so the title is part of the key.
    seen, out = set(), []
    for item in items:
        key = (norm_url(item["url"]), item["title"].strip())
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def summary(item):
    return item["summary_zh"] + ("（未读到正文）" if not item["full_text_read"] else "")


def row(item):
    return (f"| {item['date']} | {cell(item['source'])} | {item['type']} "
            f"| [{cell(item['title'])}]({item['url']}) | {cell(summary(item))} "
            f"| {cell(', '.join(item['tags']))} | {item['difficulty']} | {item['relevance']} |")


HEADER = ("| 日期 | 来源 | 类型 | 标题 | 摘要 | 标签 | 难度 | 相关度 |\n"
          "|---|---|---|---|---|---|---|---|")


def by_category(items):
    """One table per category, in CATEGORIES order; items keep their incoming order."""
    lines = []
    for cid, name in CATEGORIES.items():
        rows = [row(i) for i in items if i["category"] == cid]
        if rows:
            lines += ["", f"### {name}（{len(rows)} 条）", "", HEADER] + rows
    return lines


def render_readme(items):
    shown = [i for i in items if i["relevance"] >= 1]
    digests = sorted(DIGESTS.glob("20*.md"), reverse=True)[:8] if DIGESTS.exists() else []
    jobs = sorted(DIGESTS.glob("jobs-*.md"), reverse=True) if DIGESTS.exists() else []
    lines = ["# AI Engineer 资源追踪", "",
             "每周自动更新的 LLM / AI Engineer 论文、博客、文档和新模型用法，重点关注 agent。", ""]
    if digests:
        lines += ["## 最近周报", ""] + [f"- [{p.stem}](digests/{p.name})" for p in digests] + [""]
    if jobs:
        lines += ["## 岗位技能快照", ""] + [f"- [{p.stem.removeprefix('jobs-')}](digests/{p.name})" for p in jobs] + [""]
    lines += [f"## 全部资源（{len(shown)} 条）"] + by_category(shown)
    return "\n".join(lines) + "\n"


def load_snapshot(path):
    return {f"{s['repo']}/{s['name']}": s for s in json.loads(path.read_text())["skills"]}


def skill_line(s, extra=""):
    return f"| [{cell(s['name'])}]({s['url']}) | {cell(s['repo'])} | {s['installs']:,} | {cell(s['desc_zh'])} |{extra}"


def skill_section(current, previous, title, since=""):
    """Markdown for a skill ranking: top 20 all-time, new entries, fastest growing (vs the `since` snapshot)."""
    head = "| Skill | 仓库 | 安装量 | 说明 |\n|---|---|---|---|"
    top = sorted(current.values(), key=lambda s: -s["installs"])[:20]
    lines = [f"## {title}", "", "### 最多使用（总安装量前 20）", "", head] + [skill_line(s) for s in top]
    if previous is None:
        lines += ["", "_还没有更早的快照，下次运行起才有「新上榜」和「增长最快」。_"]
        return lines
    vs = f"（对比 {since}）" if since else ""
    new = sorted((s for k, s in current.items() if k not in previous), key=lambda s: -s["installs"])
    lines += ["", f"### 新上榜{vs}", ""]
    lines += [head] + [skill_line(s) for s in new] if new else ["_没有新上榜的 skill。_"]
    growth = sorted(((s["installs"] - previous[k]["installs"], s) for k, s in current.items() if k in previous),
                    key=lambda x: -x[0])[:10]
    lines += ["", f"### 增长最快{vs}", "", "| Skill | 仓库 | 安装量 | 说明 | 增长 |", "|---|---|---|---|---|"]
    lines += [skill_line(s, f" +{d:,} |") for d, s in growth]
    return lines


def snapshots_before(date):
    if not SKILLS.exists():
        return []
    return sorted(p for p in SKILLS.glob("*.json") if p.stem < date.isoformat())


def render_digest(items, date, unread):
    week = sorted((i for i in items if i["added"] == date.isoformat() and i["relevance"] >= 1),
                  key=lambda i: (-i["relevance"], i["date"]))
    shown = [i for i in week if i["relevance"] >= DIGEST_MIN]
    intro = f"本周新增 {len(week)} 条。"
    if len(shown) < len(week):
        intro += (f"下面只列 {DIGEST_MIN} 分以上的 {len(shown)} 条，其余 {len(week) - len(shown)} 条"
                  f"在 [README 总表]({REPO_URL}/README.md)里。")
    lines = [f"# AI Engineer 周报 {date.isoformat()}", "", intro, ""]
    must = [i for i in week if i["relevance"] >= 4][:5]
    if must:
        lines += ["## 本周必读", ""]
        lines += [f"- [ ] **[{i['title']}]({i['url']})**（{i['source']}）：{summary(i)}" for i in must] + [""]
    jobs = JOBS / f"{date:%Y-%m}.json"
    job_snap = json.loads(jobs.read_text()) if jobs.exists() else None
    # Only the digest of the week the snapshot was taken shows it, not every digest of the month.
    if job_snap and date - dt.timedelta(days=7) < dt.date.fromisoformat(job_snap["date"]) <= date:
        total, month_before, _, gaps = job_stats(job_snap, items)
        lines += ["## 本月岗位技能", "", gap_text(gaps, total, month_before), "",
                  f"完整报告：[jobs-{jobs.stem}]({REPO_URL}/digests/jobs-{jobs.stem}.md)", ""]
    lines += ["## 本周新增"] + (by_category(shown) or ["", f"_没有 {DIGEST_MIN} 分以上的新条目。_"]) + [""]
    snap = SKILLS / f"{date.isoformat()}.json"
    if snap.exists():
        prev = snapshots_before(date)
        lines += skill_section(load_snapshot(snap), load_snapshot(prev[-1]) if prev else None, "本周 Skill 榜",
                               prev[-1].stem if prev else "") + [""]
        trending = json.loads(snap.read_text()).get("trending", [])
        if trending:
            lines += ["### 近期热门（skills.sh Trending 榜）", "", "| Skill | 仓库 | 榜上安装量 | 说明 |", "|---|---|---|---|"]
            lines += [skill_line(s) for s in trending] + [""]
    unread_items = [i for i in shown if not i["full_text_read"]]
    if unread or unread_items:
        lines += ["## 没读到的内容", ""]
        lines += [f"- 来源未读取：{s}" for s in unread]
        lines += [f"- 未读到正文：[{i['title']}]({i['url']})" for i in unread_items]
    return "\n".join(lines) + "\n"


def render_month(date):
    """Monthly skill ranking: this snapshot vs the last snapshot before the month started."""
    month_start = date.replace(day=1)
    prev = snapshots_before(month_start)
    lines = skill_section(load_snapshot(SKILLS / f"{date.isoformat()}.json"),
                          load_snapshot(prev[-1]) if prev else None,
                          f"{date:%Y-%m} Skill 月榜", prev[-1].stem if prev else "")
    return "\n".join([f"# Skill 月榜 {date:%Y-%m}", ""] + lines) + "\n"


def job_stats(snap, items):
    """Per category: postings that mention it, up to 3 phrases (one per company, so one company's
    template can't fill the column), and must-read articles (relevance >= 4) added the month before."""
    first = dt.date.fromisoformat(snap["date"]).replace(day=1)
    prev = f"{first - dt.timedelta(days=1):%Y-%m}"
    postings = snap["postings"]
    stats = []
    for cid, name in CATEGORIES.items():
        typical = {}
        for p in postings:
            for s in p["skills"]:
                if s["category"] == cid:
                    typical.setdefault(p["company"], s["phrase"])
        stats.append({
            "id": cid, "name": name,
            "posts": sum(any(s["category"] == cid for s in p["skills"]) for p in postings),
            "phrases": list(typical.values())[:3],
            "articles": sum(i["relevance"] >= 4 and i.get("category") == cid and i["added"].startswith(prev)
                            for i in items),
        })
    stats.sort(key=lambda s: -s["posts"])  # stable, so ties keep CATEGORIES order
    gaps = [s for s in stats if s["id"] not in NOT_COMPARED
            and s["posts"] * 2 >= len(postings) and s["articles"] < 3]
    return len(postings), prev, stats, gaps


def gap_text(gaps, total, prev):
    rule = f"至少一半职位提到、但 {prev} 收的 4 分以上文章不到 3 篇"
    if not gaps:
        return f"_没有{rule}的类别。_"
    return f"{rule}：" + "；".join(f"{s['name']}（{s['posts']}/{total} 份职位，{s['articles']} 篇文章）"
                                 for s in gaps) + "。"


def validate_jobs(path, snap):
    """Like validate() for items: a ValueError naming the file and posting, which the weekly run knows to fix."""
    if not snap.get("postings"):
        raise ValueError(f"{path}: no postings; delete the file so next week's run takes the snapshot again")
    if str(snap.get("date"))[:7] != path.stem:
        raise ValueError(f"{path}: date {snap.get('date')} is not in {path.stem}; name the file data/jobs/<YYYY-MM of date>.json")
    dt.date.fromisoformat(snap["date"])
    for p in snap["postings"]:
        missing = [k for k in ("company", "title", "location", "url", "posted", "via", "skills") if k not in p]
        if missing:
            raise ValueError(f"{path}: {p.get('url', p)}: missing {missing}")
        for s in p["skills"] or [{}]:
            if not (isinstance(s.get("category"), str) and s["category"] in CATEGORIES and s.get("phrase")):
                raise ValueError(f"{path}: {p['url']}: each skill needs a phrase and a category from {list(CATEGORIES)}")
    over = sorted(c for c, n in Counter(p["company"] for p in snap["postings"]).items() if n > MAX_PER_COMPANY)
    if over:
        raise ValueError(f"{path}: more than {MAX_PER_COMPANY} postings from {over}")


def render_jobs(path, snap, items):
    """Monthly job-skill report: which categories postings ask for vs the must-read articles of the month before."""
    companies = Counter(p["company"] for p in snap["postings"])
    total, prev, stats, gaps = job_stats(snap, items)
    lines = [f"# 岗位技能快照 {path.stem}", "",
             f"{snap['date']} 读了 {total} 份 AI Engineer 类职位：" + "、".join(f"{c} {n}" for c, n in companies.items()) + "。",
             "", "## 各类技能被几份职位提到", "",
             f"| 类别 | 提到的职位 | 典型原话（每家公司一句） | {prev} 收的 4 分以上文章 |", "|---|---|---|---|"]
    lines += [f"| {s['name']} | {s['posts']}/{total} | {cell('；'.join(s['phrases']))} "
              f"| {'不参与对比' if s['id'] in NOT_COMPARED else s['articles']} |" for s in stats]
    lines += ["", "### 学习缺口", "", gap_text(gaps, total, prev), "",
              "_" + "".join(f"「{CATEGORIES[c]}」" for c in CATEGORIES if c in NOT_COMPARED)
              + "在职位里指编程语言和通用开发、客户交付、年限和沟通，读文章补不了，所以只列出来，不参与对比。_"]
    lines += ["", "## 职位列表", "", "| 公司 | 职位 | 地点 | 发布日期 | 来源 | 技能类别 |", "|---|---|---|---|---|---|"]
    lines += [f"| {cell(p['company'])} | [{cell(p['title'])}]({p['url']}) | {cell(p['location'])} "
              f"| {cell(p['posted'])} | {cell(p['via'])} "
              f"| {'；'.join(name for c, name in CATEGORIES.items() if any(s['category'] == c for s in p['skills']))} |"
              for p in snap["postings"]]
    if snap.get("note"):
        lines += ["", f"_{snap['note']}_"]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="run date (default: today)")
    ap.add_argument("--unread", action="append", default=[], help="source that could not be read")
    ap.add_argument("--no-state", action="store_true", help="re-render only: leave data/state.json as it is")
    args = ap.parse_args()
    date = dt.date.fromisoformat(args.date)
    names = [s["name"] for s in json.loads(SOURCES.read_text())] + ["skills.sh"]
    unknown = sorted(set(args.unread) - set(names))
    if unknown:
        raise ValueError(f"--unread {unknown}: use a name from sources.json, or skills.sh")
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    # On a same-day rerun, a source read by the earlier run is not unread today.
    unread = [s for s in args.unread if state.get(s) != date.isoformat()]

    items = json.loads(ITEMS.read_text()) if ITEMS.exists() else []
    for item in items:
        validate(item)
    items = sorted(dedupe(items), key=lambda i: i["date"], reverse=True)
    ITEMS.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n")

    DIGESTS.mkdir(exist_ok=True)
    seen = {}  # each posting is read once: a later snapshot only takes postings no earlier one has
    for path in sorted(JOBS.glob("*.json")) if JOBS.exists() else []:
        snap = json.loads(path.read_text())
        validate_jobs(path, snap)
        for p in snap["postings"]:
            if norm_url(p["url"]) in seen:
                raise ValueError(f"{path}: {p['url']} is already in {seen[norm_url(p['url'])]}")
            seen[norm_url(p["url"])] = path.name
        (DIGESTS / f"jobs-{path.stem}.md").write_text(render_jobs(path, snap, items))
    (DIGESTS / f"{date.isoformat()}.md").write_text(render_digest(items, date, unread))
    # The last weekly run of a month also writes the monthly skill ranking.
    if (date + dt.timedelta(days=7)).month != date.month and (SKILLS / f"{date.isoformat()}.json").exists():
        (DIGESTS / f"skills-{date:%Y-%m}.md").write_text(render_month(date))
    (ROOT / "README.md").write_text(render_readme(items))
    if not args.no_state:
        # max() so a same-day rerun or an older --date never moves a source backwards.
        state.update({n: max(state.get(n, ""), date.isoformat()) for n in names if n not in args.unread})
        for n in args.unread:  # never read yet: keep its first window (D-7) until a run reads it
            state.setdefault(n, (date - dt.timedelta(days=7)).isoformat())
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(items)} items; digest digests/{date.isoformat()}.md")


if __name__ == "__main__":
    main()
