import json
import xml.etree.ElementTree as ET

from radar import pipeline, site, store

SCORE = pipeline.load_toml("score.toml")
SITE = pipeline.load_toml("site.toml")
BOARDS = SITE["boards"]
NASTY = '<script>alert("x")</script> & "quotes" | pipe'


def obs(repo_id, description="A tool", license_spdx="MIT"):
    return {"repo_id": repo_id, "full_name": f"o/r{repo_id}", "stars": 3000, "forks": 1, "archived": False,
            "fork": False, "created_at": "2026-01-01T00:00:00Z", "pushed_at": "2026-10-25T00:00:00Z",
            "license_spdx": license_spdx, "description": description, "language": "Python", "topics": ["ai"],
            "observed_at": "2026-10-26T06:00:00+08:00", "stars_source": "repo_api", "origins": ["trending"],
            "open_issues": 0, "homepage": None, "renamed_from": None}


def run_doc(date="2026-10-26", complete=True, trending_ok=True, started="06:00:00", description="A tool"):
    status = "ok" if trending_ok else "failed"
    return {
        "business_date": date, "run_id": f"{date.replace('-', '')}T{started.replace(':', '')}-local",
        "started_at": f"{date}T{started}+08:00", "complete": complete,
        "sources": [{"source_id": "all-daily", "kind": "trending", "period": "daily", "status": status},
                    {"source_id": "new-30d", "kind": "search", "status": status}],
        "source_hits": [{"source_id": "all-daily", "rank": 1, "repo_id": 1, "full_name": "o/r1",
                         "period_stars": 500, "page_stars": 3000}],
        "observations": [obs(1, description=description)],
    }


def trigger(schedule=None, event="schedule"):
    return {"event": event, "schedule": schedule, "scheduled_for": None, "delay_minutes": None}


def build(tmp_path, runs, trig=None, date="2026-10-26"):
    data, out = tmp_path / "data", tmp_path / "out"
    for r in runs:
        store.write_json(data / store.run_path(r["business_date"], r["run_id"]), r)
    data.mkdir(exist_ok=True)
    ed = pipeline.maybe_build_edition(data, out, date, None, trig or trigger("17 2 * * *"), SCORE, BOARDS,
                                      now=lambda: store.datetime.fromisoformat(f"{date}T07:00:00+08:00"))
    return ed, data, out


def test_complete_run_gives_normal_edition_once(tmp_path):
    ed, data, out = build(tmp_path, [run_doc(complete=False, started="02:20:00"), run_doc(started="06:20:00")])
    assert ed["status"] == "normal" and ed["run_id"].startswith("20261026T062000")
    assert ed["picks"][0]["reasons"][0] == "GitHub Trending 顯示今日 +500 星（全站日榜第 1 名）"
    # 提交到資料分支之後，同一天再觸發也不會重做
    store.write_json(data / store.edition_path("2026-10-26"), ed)
    again = pipeline.maybe_build_edition(data, tmp_path / "out2", "2026-10-26", run_doc(), trigger(), SCORE, BOARDS)
    assert again is None


def test_partial_run_waits_until_last_trigger(tmp_path):
    partial = run_doc(complete=False)
    assert build(tmp_path / "a", [partial])[0] is None
    ed = build(tmp_path / "b", [partial], trigger(SCORE["last_schedule"]))[0]
    assert ed["status"] == "degraded"
    failed = run_doc(complete=False, trending_ok=False)
    assert build(tmp_path / "c", [failed], trigger(SCORE["last_schedule"]))[0] is None


def test_site_escapes_and_links_work_under_subpath(tmp_path):
    ed, data, _ = build(tmp_path, [run_doc(description=NASTY)])
    store.write_json(data / store.edition_path("2026-10-26"), ed)
    out = tmp_path / "site"
    site.build_site(data, out, SITE, SCORE, "abc123")

    home = (out / "index.html").read_text(encoding="utf-8")
    assert "<script>alert" not in home and "&lt;script&gt;alert" in home
    day = (out / "archive/2026/10/26/index.html").read_text(encoding="utf-8")
    assert 'href="../../../../static/style.css"' in day and 'href="../../../../about/#g-star"' in day
    assert '<link rel="canonical" href="https://yaochen0506.github.io/githubseeker/archive/2026/10/26/">' in day

    feed = ET.fromstring((out / "feed.xml").read_bytes())
    item = feed.find("channel/item")
    assert item.find("guid").text == "radar:2026-10-26:1"
    assert item.find("link").text == "https://yaochen0506.github.io/githubseeker/archive/2026/10/26/#repo-1"
    assert NASTY in item.find("description").text  # 解析後還原成原文，代表跳脫正確
    self_link = feed.find("channel/{http://www.w3.org/2005/Atom}link")
    assert self_link.get("href") == "https://yaochen0506.github.io/githubseeker/feed.xml"

    assert json.loads((out / "data/latest.json").read_text(encoding="utf-8"))["picks"][0]["description"] == NASTY
    assert json.loads((out / "edition.json").read_text(encoding="utf-8")) == {
        "edition_id": "2026-10-26", "generated_at": ed["generated_at"], "code_version": "abc123"}
    locs = [e.text for e in ET.fromstring((out / "sitemap.xml").read_bytes()).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert "https://yaochen0506.github.io/githubseeker/archive/2026/10/26/" in locs


def test_feed_guid_and_date_stable_across_rebuilds(tmp_path):
    ed, data, _ = build(tmp_path, [run_doc()])
    store.write_json(data / store.edition_path("2026-10-26"), ed)
    site.build_site(data, tmp_path / "s1", SITE, SCORE, "v1")
    site.build_site(data, tmp_path / "s2", SITE, SCORE, "v2")
    assert (tmp_path / "s1/feed.xml").read_bytes() == (tmp_path / "s2/feed.xml").read_bytes()


def test_no_secrets_in_site_output(tmp_path):
    ed, data, _ = build(tmp_path, [run_doc()])
    store.write_json(data / store.edition_path("2026-10-26"), ed)
    out = tmp_path / "site"
    site.build_site(data, out, SITE, SCORE, "v1")
    for path in out.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not any(s in text for s in ("ghp_", "github_pat_", "gho_", "ghs_", "Bearer ")), path


def test_empty_site_still_builds(tmp_path):
    (tmp_path / "data").mkdir()
    site.build_site(tmp_path / "data", tmp_path / "site", SITE, SCORE, "v1")
    assert "第一份精選還在準備中" in (tmp_path / "site/index.html").read_text(encoding="utf-8")


def test_needs_deploy_compares_edition_and_code():
    expected = {"edition_id": "2026-10-26", "generated_at": "x", "code_version": "v2"}
    assert site.needs_deploy(None, expected)
    assert site.needs_deploy({"edition_id": "2026-10-26", "code_version": "v1"}, expected)
    assert not site.needs_deploy({"edition_id": "2026-10-26", "code_version": "v2"}, expected)


def test_release_notes_list_picks_and_escape_markdown():
    edition = {"edition_id": "2026-10-28", "status": "normal", "picks": [
        {"slot": "main", "position": 1, "full_name": "o/r1", "html_url": "https://github.com/o/r1",
         "reasons": ["GitHub Trending 顯示今日 +500 星（全站日榜第 1 名）"],
         "summary": {"status": "done", "content": {"one_liner": "把 [連結](x) 變成 *粗體* 的工具"}}},
        {"slot": "discovery", "position": 1, "full_name": "o/r2", "html_url": "https://github.com/o/r2",
         "reasons": ["建立 3 天，已有 900 星"], "summary": {"status": "failed", "content": None}},
    ]}
    title, body = site.release_notes(edition, SITE)
    assert title == "2026-10-28 精選"
    assert "https://yaochen0506.github.io/githubseeker/archive/2026/10/28/" in body
    assert r"1. **[o/r1](https://github.com/o/r1)**：把 \[連結\]\(x\) 變成 \*粗體\* 的工具" in body
    assert "### 新發現" in body and "建立 3 天，已有 900 星" in body


def test_validate_tree_accepts_edition(tmp_path):
    ed, _, out = build(tmp_path, [run_doc()])
    assert store.validate_tree(out) == 1
