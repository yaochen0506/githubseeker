"""E2：重複標示、今日清單、專案名錄。"""

import json
import xml.etree.ElementTree as ET

from radar import pipeline, site, store

SITE = pipeline.load_toml("site.toml")
SCORE = pipeline.load_toml("score.toml")
CATS = pipeline.load_toml("categories.toml")


def pick(repo_id, slot="main", category=None, repeat_of=None, one_liner=None):
    summary = ({"status": "done", "content": {"one_liner": one_liner, "barrier": "online", "features": ["f"],
                                              "audience": "a", "use_cases": ["u"], "barrier_note": "n",
                                              "limitations": "l", "based_on": ["readme"]},
                "has_readme": True} if one_liner else {"status": "failed", "content": None})
    return {"slot": slot, "position": 1, "category": category, "repo_id": repo_id, "full_name": f"o/r{repo_id}",
            "html_url": f"https://github.com/o/r{repo_id}", "description": "desc", "language": "Python",
            "topics": [], "license_spdx": "MIT", "created_at": "2026-01-01T00:00:00Z",
            "pushed_at": "2026-11-01T00:00:00Z", "stars": 100, "forks": 1, "metric_method": "observed",
            "growth_per_day": 10.0, "score": 50.0, "score_breakdown": None, "trending_hits": [],
            "observed": None, "reasons": [f"理由 {repo_id}"], "notes": [], "repeat_of": repeat_of,
            "repeat_reason": None, "summary": summary}


def edition(day, *picks):
    return {"schema_version": "1", "score_version": "d2", "edition_id": day, "business_date": day,
            "generated_at": f"{day}T07:00:00+08:00", "run_id": "x", "status": "normal",
            "coverage": {"sources_ok": [], "sources_failed": []}, "picks": list(picks), "cooldown_excluded": [],
            "candidate_counts": {"main": 0, "discovery": 0}}


EDITIONS = [
    edition("2026-11-01", pick(1, one_liner="記帳 App"), pick(2)),
    edition("2026-11-12", pick(1, one_liner="記帳的 App"), pick(3, slot="category", category="fun-creative",
                                                             one_liner="像素遊戲")),
    edition("2026-11-20", pick(1, repeat_of="2026-11-12", one_liner="記帳 App 新版"), pick(4, category="learning"),
            pick(3, slot="category", category="fun-creative", one_liner="像素遊戲")),
]


def test_repeat_labels_count_earlier_editions_only():
    seen = site.appearances(EDITIONS)
    assert seen[1] == ["2026-11-01", "2026-11-12", "2026-11-20"]
    assert site.repeat_info(pick(4), "2026-11-20", seen)["repeat_label"] == "首次"
    assert site.repeat_info(pick(3), "2026-11-20", seen)["repeat_label"] == "第 2 次入選（上次 11/12）"
    burst = site.repeat_info(pick(1, repeat_of="2026-11-12"), "2026-11-20", seen)
    assert burst["repeat_kind"] == "burst" and burst["repeat_label"] == "成長爆發，第 3 次（上次 11/12）"
    # 歷史頁看的是當天的狀況：11/01 那天專案 1 還是首次
    assert site.repeat_info(pick(1), "2026-11-01", seen)["repeat_label"] == "首次"


def test_directory_groups_once_per_repo_and_skips_excluded():
    state = {"2": {"category": "excluded"}, "1": {"category": "everyday-tools"}}
    groups = site.build_directory(EDITIONS, CATS, state, SITE["base_url"])
    by_id = {g["id"]: g for g in groups}
    tools = by_id["everyday-tools"]["entries"]
    assert [e["full_name"] for e in tools] == ["o/r1"]
    assert tools[0]["times"] == 3 and tools[0]["first"] == "2026-11-01" and tools[0]["one_liner"] == "記帳 App 新版"
    assert tools[0]["dates"][0]["url"].endswith("archive/2026/11/01/#repo-1")
    assert [e["full_name"] for e in by_id["fun-creative"]["entries"]] == ["o/r3"]
    assert "uncategorized" not in by_id or all(e["repo_id"] != 2 for e in by_id["uncategorized"]["entries"])
    assert all(e["repo_id"] != 2 for g in groups for e in g["entries"])  # 不收錄的不列


def test_site_shows_today_list_tags_and_directory(tmp_path):
    data = tmp_path / "data"
    for e in EDITIONS:
        store.write_json(data / store.edition_path(e["edition_id"]), e)
    store.write_json(data / "state" / "categories.json",
                     {"schema_version": "1", "prompt_version": "e1", "updated_at": "x",
                      "repos": {"1": {"category": "everyday-tools", "exclude_reason": None, "full_name": "o/r1",
                                      "classified_at": "x"}}})
    out = tmp_path / "site"
    site.build_site(data, out, SITE, SCORE, "v1", CATS)

    home = (out / "index.html").read_text(encoding="utf-8")
    today = home[home.index('id="h-today"'):home.index('id="h-main"')]
    assert "共 3 個，其中 2 個以前入選過" in today
    assert 'href="#repo-1"' in today and "成長爆發，第 3 次（上次 11/12）" in today and "首次" in today
    assert 'class="tag tag-repeat">第 2 次入選（上次 11/12）' in home

    directory = (out / "directory" / "index.html").read_text(encoding="utf-8")
    assert 'id="dir-search"' in directory and 'id="dir-everyday-tools"' in directory
    assert 'data-search="o/r1 記帳 app 新版 desc"' in directory
    data_json = json.loads((out / "data" / "directory.json").read_text(encoding="utf-8"))
    assert {g["id"] for g in data_json} >= {"everyday-tools", "fun-creative"}
    assert "directory/" in (out / "sitemap.xml").read_text(encoding="utf-8")

    titles = [i.find("title").text for i in ET.fromstring((out / "feed.xml").read_bytes()).iter("item")]
    assert any("（第 2 次入選（上次 11/12））" in t for t in titles)
