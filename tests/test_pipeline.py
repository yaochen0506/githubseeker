from datetime import datetime

from radar import pipeline, store
from radar.sources import GitHubApi

from conftest import (DAILY_URL, SEARCH_URL, WEEKLY_URL, FakeResponse, FakeSession, api, config, page_html,
                      repo_json, row_html, search_json)


def run(tmp_path, routes, started, cfg=None, data_files=None, force=False):
    data_dir, out_dir = tmp_path / "data", tmp_path / "out"
    data_dir.mkdir(exist_ok=True)
    for rel, doc in (data_files or {}).items():
        store.write_json(data_dir / rel, doc)
    cfg = cfg or config()
    session = FakeSession(routes)
    gh = GitHubApi(session, max_requests=cfg["max_rest_requests"], sleep=lambda s: None)
    doc = pipeline.collect(cfg, "0" * 64, data_dir, out_dir, gh, session, started,
                           pipeline.trigger_info(started, "test", None), "test-sha", "local",
                           force=force, sleep=lambda s: None, now=lambda: started)
    return doc, out_dir, session


def tracking_doc(*entries):
    return {"schema_version": "1", "updated_at": "2026-10-25T06:00:00+08:00",
            "repos": [{"repo_id": i, "full_name": n, "first_seen": "2026-10-20", "last_seen": "2026-10-25"}
                      for i, n in entries]}


def test_same_repo_across_sources_is_one_observation_with_many_hits(tmp_path, started):
    routes = {
        DAILY_URL: FakeResponse(200, text=page_html(row_html("a/one", 1, "10 stars today"))),
        WEEKLY_URL: FakeResponse(200, text=page_html(row_html("a/one", 1, "70 stars this week"))),
        SEARCH_URL: FakeResponse(200, search_json(repo_json(1, "a/one"))),
        api("/repositories/1"): FakeResponse(200, repo_json(1, "a/one", stars=500)),
    }
    doc, out_dir, session = run(tmp_path, routes, started)
    assert len(doc["observations"]) == 1 and len(doc["source_hits"]) == 3
    assert doc["observations"][0]["origins"] == ["trending", "search"]
    assert doc["observations"][0]["stars_source"] == "repo_api"
    assert session.calls.count(api("/repositories/1")) == 1
    assert doc["complete"] is True
    assert store.validate_tree(out_dir) == 3


def test_renamed_repo_keeps_same_id(tmp_path, started):
    routes = {
        DAILY_URL: FakeResponse(200, text=page_html(row_html("old/name", 7))),
        WEEKLY_URL: FakeResponse(200, text=page_html(row_html("x/y", 8, "1 stars this week"))),
        SEARCH_URL: FakeResponse(200, search_json()),
        api("/repositories/7"): FakeResponse(200, repo_json(7, "new/name")),
        api("/repos/old/name"): FakeResponse(200, repo_json(7, "new/name")),
        api("/repositories/8"): FakeResponse(200, repo_json(8, "x/y")),
    }
    doc, _, _ = run(tmp_path, routes, started)
    obs = {o["repo_id"]: o for o in doc["observations"]}
    assert obs[7]["full_name"] == "new/name" and obs[7]["renamed_from"] == "old/name"


def test_missing_page_id_falls_back_to_name(tmp_path, started):
    routes = {
        DAILY_URL: FakeResponse(200, text=page_html(row_html("a/one", None))),
        WEEKLY_URL: FakeResponse(200, text=page_html(row_html("a/one", None, "3 stars this week"))),
        SEARCH_URL: FakeResponse(200, search_json()),
        api("/repos/a/one"): FakeResponse(200, repo_json(11, "a/one")),
    }
    doc, _, session = run(tmp_path, routes, started)
    assert [o["repo_id"] for o in doc["observations"]] == [11]
    assert all(h["repo_id"] == 11 for h in doc["source_hits"])
    assert session.calls.count(api("/repos/a/one")) == 1


def test_trending_down_still_observes_tracking(tmp_path, started):
    routes = {
        DAILY_URL: FakeResponse(503),
        WEEKLY_URL: FakeResponse(200, text="<html>layout changed</html>"),
        SEARCH_URL: FakeResponse(200, search_json()),
        api("/repositories/5"): FakeResponse(200, repo_json(5, "t/five")),
    }
    doc, out_dir, _ = run(tmp_path, routes, started, data_files={"state/tracking.json": tracking_doc((5, "t/five"))})
    assert doc["complete"] is False
    assert [o["repo_id"] for o in doc["observations"]] == [5]
    assert doc["observations"][0]["origins"] == ["tracking"]
    status = store.read_json(out_dir / "status.json")
    assert status["last_run_complete"] is False and status["last_complete_run_id"] is None


def test_deleted_tracked_repo_is_dropped_but_run_stays_complete(tmp_path, started):
    routes = {
        DAILY_URL: FakeResponse(200, text=page_html(row_html("a/one", 1))),
        WEEKLY_URL: FakeResponse(200, text=page_html(row_html("a/one", 1, "9 stars this week"))),
        SEARCH_URL: FakeResponse(200, search_json()),
        api("/repositories/1"): FakeResponse(200, repo_json(1, "a/one")),
        api("/repositories/9"): FakeResponse(404),
    }
    doc, out_dir, _ = run(tmp_path, routes, started, data_files={"state/tracking.json": tracking_doc((9, "gone/repo"))})
    assert doc["unresolved"] == [{"repo_id": 9, "full_name": "gone/repo", "reason": "not_found", "detail": "http_404"}]
    assert doc["complete"] is True
    assert [e["repo_id"] for e in store.read_json(out_dir / "state/tracking.json")["repos"]] == [1]


def test_rate_limit_stops_further_calls_without_crashing(tmp_path, started):
    limited = FakeResponse(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"})
    routes = {
        DAILY_URL: FakeResponse(200, text=page_html(row_html("a/one", 1), row_html("a/two", 2))),
        WEEKLY_URL: FakeResponse(200, text=page_html(row_html("a/one", 1, "2 stars this week"))),
        SEARCH_URL: FakeResponse(200, search_json()),
        api("/repositories/1"): limited,
    }
    doc, _, session = run(tmp_path, routes, started)
    assert [u["reason"] for u in doc["unresolved"]] == ["rate_limited", "rate_limited"]
    assert api("/repositories/2") not in session.calls
    assert doc["complete"] is False


def test_search_fallback_when_budget_runs_out(tmp_path, started):
    cfg = config()
    cfg["max_rest_requests"] = 1  # 只夠呼叫 Search 一次
    routes = {
        DAILY_URL: FakeResponse(200, text=page_html(row_html("a/one", 1))),
        WEEKLY_URL: FakeResponse(200, text=page_html(row_html("a/one", 1, "1 stars this week"))),
        SEARCH_URL: FakeResponse(200, search_json(repo_json(20, "s/new", stars=80))),
    }
    doc, _, _ = run(tmp_path, routes, started, cfg=cfg)
    obs = {o["repo_id"]: o for o in doc["observations"]}
    assert obs[20]["stars_source"] == "search_api"
    assert doc["unresolved"][0]["reason"] == "budget_exhausted"


def test_skips_when_complete_run_exists_today(tmp_path, started):
    existing = {"runs/2026/10/26/20261026T021700-1.1.json": {"complete": True}}
    doc, out_dir, session = run(tmp_path, {}, started, data_files=existing)
    assert doc is None and not out_dir.exists() and session.calls == []


def test_reruns_when_only_partial_run_exists(tmp_path, started):
    existing = {"runs/2026/10/26/20261026T021700-1.1.json": {"complete": False}}
    routes = {
        DAILY_URL: FakeResponse(200, text=page_html(row_html("a/one", 1))),
        WEEKLY_URL: FakeResponse(200, text=page_html(row_html("a/one", 1, "1 stars this week"))),
        SEARCH_URL: FakeResponse(200, search_json()),
        api("/repositories/1"): FakeResponse(200, repo_json(1, "a/one")),
    }
    doc, _, _ = run(tmp_path, routes, started, data_files=existing)
    assert doc["complete"] is True


def test_trigger_info_measures_delay_in_taipei_time():
    started = datetime(2026, 10, 26, 7, 5, 0, tzinfo=store.TAIPEI)
    info = pipeline.trigger_info(started, "schedule", "17 2 * * *")
    assert info["scheduled_for"] == "2026-10-26T02:17:00+08:00" and info["delay_minutes"] == 288.0
    late = pipeline.trigger_info(datetime(2026, 10, 27, 1, 0, tzinfo=store.TAIPEI), "schedule", "17 10 * * *")
    assert late["scheduled_for"] == "2026-10-26T10:17:00+08:00"


def test_validate_tree_rejects_unexpected_files(tmp_path):
    store.write_json(tmp_path / "evil.json", {})
    try:
        store.validate_tree(tmp_path)
    except ValueError as exc:
        assert "不允許" in str(exc)
    else:
        raise AssertionError("應該拒絕")


def test_tracking_drops_stale_and_caps_size():
    entries = [{"repo_id": i, "full_name": f"o/r{i}", "first_seen": "2026-10-01", "last_seen": d}
               for i, d in [(1, "2026-10-01"), (2, "2026-10-20"), (3, "2026-10-25")]]
    kept = store.update_tracking(entries, [(4, "o/r4")], set(), "2026-10-26", max_size=2, max_age_days=14)
    assert [e["repo_id"] for e in kept] == [4, 3]
