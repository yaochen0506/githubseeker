from radar import sources
from radar.sources import GitHubApi, RateLimited

from conftest import DAILY_URL, FakeResponse, FakeSession, api, page_html, repo_json, row_html


def test_parse_reads_id_name_and_period_stars():
    rows = sources.parse_trending(page_html(row_html("octo/cat", 42, "1,234 stars today", "9,876")), "daily")
    assert rows == [{"rank": 1, "repo_id": 42, "full_name": "octo/cat", "period_stars": 1234, "page_stars": 9876}]


def test_period_stars_null_when_missing_or_mismatched_but_zero_kept():
    html = page_html(
        row_html("a/one", 1, "5 stars today"),      # 週榜頁面卻寫 today：對不上 → null
        row_html("a/two", 2, None),                 # 沒有期間數字 → null
        row_html("a/three", 3, "0 stars this week"),  # 真的是 0 → 0
    )
    rows = sources.parse_trending(html, "weekly")
    assert [r["period_stars"] for r in rows] == [None, None, 0]


def test_missing_record_id_gives_none():
    rows = sources.parse_trending(page_html(row_html("a/one", None)), "daily")
    assert rows[0]["repo_id"] is None


def test_http_200_with_too_few_rows_is_failure():
    session = FakeSession({DAILY_URL: FakeResponse(200, text="<html><body>改版了</body></html>")})
    src, rows = sources.fetch_trending(session, DAILY_URL, "daily", "ua", min_rows=5, sleep=lambda s: None)
    assert src["status"] == "failed" and src["error"].startswith("too_few_rows") and rows == []


def test_trending_retries_429_then_succeeds():
    waits = []
    session = FakeSession({DAILY_URL: [FakeResponse(429, headers={"Retry-After": "3"}),
                                       FakeResponse(200, text=page_html(row_html("a/one", 1)))]})
    src, rows = sources.fetch_trending(session, DAILY_URL, "daily", "ua", min_rows=1, sleep=waits.append)
    assert src["status"] == "ok" and len(rows) == 1 and waits == [3]


def test_api_waits_for_retry_after_then_succeeds():
    waits = []
    session = FakeSession({api("/repositories/1"): [FakeResponse(403, headers={"Retry-After": "7"}),
                                                    FakeResponse(200, repo_json(1, "a/one"))]})
    gh = GitHubApi(session, sleep=waits.append)
    assert gh.get("/repositories/1")["id"] == 1 and waits == [7]


def test_api_gives_up_when_reset_is_too_far():
    session = FakeSession({api("/repositories/1"): FakeResponse(
        403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "5000"})})
    gh = GitHubApi(session, max_wait=60, sleep=lambda s: None, clock=lambda: 1000)
    try:
        gh.get("/repositories/1")
    except RateLimited:
        pass
    else:
        raise AssertionError("應該丟出 RateLimited")


def test_usage_report_uses_remaining_header_difference():
    headers = lambda remaining: {"x-ratelimit-resource": "core", "x-ratelimit-limit": "1000",
                                 "x-ratelimit-remaining": str(remaining), "x-ratelimit-reset": "99"}
    session = FakeSession({api("/repositories/1"): FakeResponse(200, repo_json(1, "a/one"), headers=headers(990)),
                           api("/repositories/2"): FakeResponse(200, repo_json(2, "a/two"), headers=headers(985))})
    gh = GitHubApi(session)
    gh.get("/repositories/1")
    gh.get("/repositories/2")
    assert gh.usage_report()["core"] == {"requests": 2, "limit": 1000, "remaining_first": 990,
                                         "remaining_last": 985, "used_by_headers": 6}
