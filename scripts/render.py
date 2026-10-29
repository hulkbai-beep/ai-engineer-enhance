"""Dedupe and validate data/items.json, then write README.md and the weekly digest.

Usage: python3 scripts/render.py [--date YYYY-MM-DD] [--unread "source A" --unread "source B"]

Reads only files inside the repo; no network access.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ITEMS = ROOT / "data" / "items.json"
SKILLS = ROOT / "data" / "skills"
DIGESTS = ROOT / "digests"

REQUIRED = ["date", "added", "source", "type", "title", "url", "summary_zh",
            "tags", "difficulty", "relevance", "full_text_read"]
TYPES = {"paper", "blog", "docs", "release"}
DIFFICULTIES = {"入门", "进阶", "研究"}


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


def dedupe(items):
    seen, out = set(), []
    for item in items:
        key = norm_url(item["url"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def row(item):
    summary = item["summary_zh"] + ("（未读到正文）" if not item["full_text_read"] else "")
    return (f"| {item['date']} | {cell(item['source'])} | {item['type']} "
            f"| [{cell(item['title'])}]({item['url']}) | {cell(summary)} "
            f"| {', '.join(item['tags'])} | {item['difficulty']} | {item['relevance']} |")


HEADER = ("| 日期 | 来源 | 类型 | 标题 | 摘要 | 标签 | 难度 | 相关度 |\n"
          "|---|---|---|---|---|---|---|---|")


def render_readme(items):
    shown = [i for i in items if i["relevance"] >= 1]
    digests = sorted(DIGESTS.glob("20*.md"), reverse=True)[:8] if DIGESTS.exists() else []
    lines = ["# AI Engineer 资源追踪", "",
             "每周自动更新的 LLM / AI Engineer 论文、博客、文档和新模型用法，重点关注 agent。", ""]
    if digests:
        lines += ["## 最近周报", ""] + [f"- [{p.stem}](digests/{p.name})" for p in digests] + [""]
    lines += [f"## 全部资源（{len(shown)} 条）", "", HEADER] + [row(i) for i in shown]
    return "\n".join(lines) + "\n"


def load_snapshot(path):
    return {f"{s['repo']}/{s['name']}": s for s in json.loads(path.read_text())["skills"]}


def skill_line(s, extra=""):
    return f"| [{cell(s['name'])}]({s['url']}) | {cell(s['repo'])} | {s['installs']:,} | {cell(s['desc_zh'])} |{extra}"


def skill_section(current, previous, title):
    """Markdown for a skill ranking: top 20 all-time, new entries, fastest growing."""
    head = "| Skill | 仓库 | 安装量 | 说明 |\n|---|---|---|---|"
    top = sorted(current.values(), key=lambda s: -s["installs"])[:20]
    lines = [f"## {title}", "", "### 最多使用（总安装量前 20）", "", head] + [skill_line(s) for s in top]
    if previous is None:
        lines += ["", "_还没有更早的快照，下次运行起才有「新上榜」和「增长最快」。_"]
        return lines
    new = sorted((s for k, s in current.items() if k not in previous), key=lambda s: -s["installs"])
    lines += ["", "### 新上榜", ""]
    lines += [head] + [skill_line(s) for s in new] if new else ["_没有新上榜的 skill。_"]
    growth = sorted(((s["installs"] - previous[k]["installs"], s) for k, s in current.items() if k in previous),
                    key=lambda x: -x[0])[:10]
    lines += ["", "### 增长最快", "", "| Skill | 仓库 | 安装量 | 说明 | 增长 |", "|---|---|---|---|---|"]
    lines += [skill_line(s, f" +{d:,} |") for d, s in growth]
    return lines


def snapshots_before(date):
    if not SKILLS.exists():
        return []
    return sorted(p for p in SKILLS.glob("*.json") if p.stem < date.isoformat())


def render_digest(items, date, unread):
    week = sorted((i for i in items if i["added"] == date.isoformat() and i["relevance"] >= 1),
                  key=lambda i: (-i["relevance"], i["date"]))
    lines = [f"# AI Engineer 周报 {date.isoformat()}", "", f"本周新增 {len(week)} 条。", ""]
    must = [i for i in week if i["relevance"] >= 4][:5]
    if must:
        lines += ["## 本周必读", ""]
        lines += [f"- **[{i['title']}]({i['url']})**（{i['source']}）：{i['summary_zh']}" for i in must] + [""]
    lines += ["## 本周新增", "", HEADER] + [row(i) for i in week] + [""]
    snap = SKILLS / f"{date.isoformat()}.json"
    if snap.exists():
        prev = snapshots_before(date)
        lines += skill_section(load_snapshot(snap), load_snapshot(prev[-1]) if prev else None, "本周 Skill 榜") + [""]
        trending = json.loads(snap.read_text()).get("trending", [])
        if trending:
            lines += ["### 24 小时热门（skills.sh Trending）", "", "| Skill | 仓库 | 24h 安装量 | 说明 |", "|---|---|---|---|"]
            lines += [skill_line(s) for s in trending] + [""]
    unread_items = [i for i in week if not i["full_text_read"]]
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
                          f"{date:%Y-%m} Skill 月榜")
    return "\n".join([f"# Skill 月榜 {date:%Y-%m}", ""] + lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="run date (default: today)")
    ap.add_argument("--unread", action="append", default=[], help="source that could not be read")
    args = ap.parse_args()
    date = dt.date.fromisoformat(args.date)

    items = json.loads(ITEMS.read_text()) if ITEMS.exists() else []
    for item in items:
        validate(item)
    items = sorted(dedupe(items), key=lambda i: i["date"], reverse=True)
    ITEMS.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n")

    DIGESTS.mkdir(exist_ok=True)
    (DIGESTS / f"{date.isoformat()}.md").write_text(render_digest(items, date, args.unread))
    # The last weekly run of a month also writes the monthly skill ranking.
    if (date + dt.timedelta(days=7)).month != date.month and (SKILLS / f"{date.isoformat()}.json").exists():
        (DIGESTS / f"skills-{date:%Y-%m}.md").write_text(render_month(date))
    (ROOT / "README.md").write_text(render_readme(items))
    print(f"{len(items)} items; digest digests/{date.isoformat()}.md")


if __name__ == "__main__":
    main()
