from datetime import date

from radar import editorial, pipeline, rank

SCORE = pipeline.load_toml("score.toml")
BOARDS = pipeline.load_toml("site.toml")["boards"]


def obs(repo_id, stars=1000, created="2026-01-01T00:00:00Z", pushed="2026-10-25T00:00:00Z",
        archived=False, fork=False, license_spdx="MIT"):
    return {"repo_id": repo_id, "full_name": f"o/r{repo_id}", "stars": stars, "forks": 1, "archived": archived,
            "fork": fork, "created_at": created, "pushed_at": pushed, "license_spdx": license_spdx,
            "description": "d", "language": "Python", "topics": [], "observed_at": "2026-10-26T06:00:00+08:00",
            "stars_source": "repo_api", "origins": ["trending"], "open_issues": 0, "homepage": None,
            "renamed_from": None}


def hit(source_id, repo_id, rank_, period_stars):
    return {"source_id": source_id, "rank": rank_, "repo_id": repo_id, "full_name": f"o/r{repo_id}",
            "period_stars": period_stars, "page_stars": None}


def run(observations, hits, failed=()):
    sources = [{"source_id": s, "kind": "trending", "period": p, "status": "failed" if s in failed else "ok"}
               for s, p in (("all-daily", "daily"), ("all-weekly", "weekly"), ("python-daily", "daily"))]
    return {"business_date": "2026-10-26", "run_id": "20261026T060000-local", "sources": sources,
            "source_hits": hits, "observations": observations}


def test_daily_preferred_then_weekly_average():
    hits = [{"period": "weekly", "period_stars": 700}, {"period": "daily", "period_stars": 90}]
    assert rank.daily_growth(hits) == (90.0, "trending_daily")
    assert rank.daily_growth(hits[:1]) == (100.0, "trending_weekly_avg")
    assert rank.daily_growth([{"period": "daily", "period_stars": None}]) == (None, None)


def test_score_caps_and_small_repo_floor():
    full, _ = rank.score(1000, 1_000_000, SCORE)
    assert 75 <= full < 76  # 絕對成長滿分，但相對成長在大專案很低
    small, breakdown = rank.score(30, 40, SCORE)  # 分母取下限 200，避免小專案比例被放大
    assert breakdown["s_rel"] == 1.0 and small < full


def test_ineligible_repos_are_skipped():
    today = date(2026, 10, 26)
    assert rank.eligible(obs(1), today, SCORE)
    assert not rank.eligible(obs(2, archived=True), today, SCORE)
    assert not rank.eligible(obs(3, fork=True), today, SCORE)
    assert not rank.eligible(obs(4, pushed="2026-06-01T00:00:00Z"), today, SCORE)
    assert not rank.eligible({**obs(5), "archived": None}, today, SCORE)


def test_main_sorted_by_score_and_discovery_by_daily_average():
    observations = [obs(1, stars=5000), obs(2, stars=5000), obs(3, stars=400, created="2026-10-21T00:00:00Z"),
                    obs(4, stars=60, created="2026-10-24T00:00:00Z"), obs(5, stars=40, created="2026-10-24T00:00:00Z"),
                    obs(6, stars=90000, created="2026-10-10T00:00:00Z")]
    hits = [hit("all-daily", 1, 2, 50), hit("all-daily", 2, 1, 500)]
    main, discovery, _, counts = rank.select(run(observations, hits), [], SCORE)
    assert [c["obs"]["repo_id"] for c in main] == [2, 1]
    # 3：400 星／5 天 = 80；4：60 星／2 天 = 30；5 未滿 50 星；6 建立超過 7 天
    assert [c["obs"]["repo_id"] for c in discovery] == [3, 4]
    assert counts == {"main": 2, "discovery": 2}


def test_quota_limits_and_fewer_candidates_mean_fewer_picks():
    observations = [obs(i) for i in range(1, 11)]
    hits = [hit("all-daily", i, i, 100 - i) for i in range(1, 11)]
    main, discovery, _, _ = rank.select(run(observations, hits), [], SCORE)
    assert len(main) == 7 and discovery == []


def test_cooldown_excludes_recent_picks_only():
    observations = [obs(1), obs(2), obs(3)]
    hits = [hit("all-daily", i, i, 100) for i in (1, 2, 3)]
    past = [{"business_date": "2026-10-23", "picks": [{"repo_id": 1}]},   # 3 天前：排除
            {"business_date": "2026-10-18", "picks": [{"repo_id": 2}]}]   # 8 天前：可再入選
    main, _, excluded, _ = rank.select(run(observations, hits), past, SCORE)
    assert [c["obs"]["repo_id"] for c in main] == [2, 3] and excluded == [1]


def test_failed_source_hits_are_ignored():
    main, _, _, _ = rank.select(run([obs(1)], [hit("all-daily", 1, 1, 100)], failed={"all-daily"}), [], SCORE)
    assert main == []


def test_reasons_are_built_from_numbers():
    hits = [{"source_id": "python-daily", "rank": 3, "period": "daily", "period_stars": 1376},
            {"source_id": "all-daily", "rank": 2, "period": "daily", "period_stars": 1376},
            {"source_id": "all-weekly", "rank": 5, "period": "weekly", "period_stars": 4000}]
    cand = {"obs": obs(1), "metric_method": "trending_daily", "growth_per_day": 1376.0, "trending_hits": hits}
    assert editorial.reasons(cand, BOARDS, "2026-10-26") == [
        "GitHub Trending 顯示今日 +1,376 星（全站日榜第 2 名）",
        "也在 Python 日榜第 3 名、全站週榜第 5 名",
    ]
    new = {"obs": obs(9, stars=400, created="2026-10-16T00:00:00Z"), "metric_method": "lifetime_avg",
           "growth_per_day": 40.0, "trending_hits": []}
    assert editorial.reasons(new, BOARDS, "2026-10-26") == ["建立 10 天，已有 400 星，建立以來平均每天 +40 星"]


def test_notes_warn_about_license_and_new_projects():
    notes = editorial.notes(obs(1, license_spdx=None, created="2026-10-24T00:00:00Z"), "2026-10-26")
    assert notes[0].startswith("未標示授權") and "建立僅 2 天" in notes[1]
    assert editorial.notes(obs(2), "2026-10-26") == []
