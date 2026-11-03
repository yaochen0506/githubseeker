"""D 階段：實際觀測成長、新發現 30 天、再次推薦例外。"""

from radar import editorial, pipeline, rank, store

SCORE = pipeline.load_toml("score.toml")
BOARDS = pipeline.load_toml("site.toml")["boards"]
NOW = "2026-11-03T06:00:00+08:00"


def obs(repo_id, stars, observed_at=NOW, created="2026-01-01T00:00:00Z", source="repo_api"):
    return {"repo_id": repo_id, "full_name": f"o/r{repo_id}", "stars": stars, "forks": 1, "archived": False,
            "fork": False, "created_at": created, "pushed_at": "2026-11-02T00:00:00Z", "license_spdx": "MIT",
            "description": "d", "language": "Python", "topics": [], "observed_at": observed_at,
            "stars_source": source, "origins": ["tracking"], "open_issues": 0, "homepage": None,
            "renamed_from": None}


def run(observations, hits=(), date="2026-11-03", started="06:00:00"):
    return {"business_date": date, "run_id": f"{date.replace('-', '')}T{started.replace(':', '')}-local",
            "started_at": f"{date}T{started}+08:00", "complete": True,
            "sources": [{"source_id": "all-daily", "kind": "trending", "period": "daily", "status": "ok"}],
            "source_hits": list(hits), "observations": observations}


def hit(repo_id, rank_, stars):
    return {"source_id": "all-daily", "rank": rank_, "repo_id": repo_id, "full_name": f"o/r{repo_id}",
            "period_stars": stars, "page_stars": None}


def hist(*entries):
    """(repo_id, observed_at, stars[, source])"""
    out = {}
    for e in entries:
        out.setdefault(e[0], []).append((e[1], e[2], e[3] if len(e) > 3 else "repo_api"))
    return out


# ---- 驗收 1：首日、次日、同日重跑、隔三天、隔五天 ----

def test_first_day_has_no_observed_growth():
    assert rank.observed_growth(obs(1, 1000), {}, SCORE) is None


def test_next_day_uses_24h_difference():
    g = rank.observed_growth(obs(1, 1500), hist((1, "2026-11-02T06:00:00+08:00", 1000)), SCORE)
    assert g["delta"] == 500 and g["hours"] == 24.0 and g["growth"] == 500


def test_same_day_rerun_is_not_a_baseline_but_older_one_is():
    h = hist((1, "2026-11-02T06:00:00+08:00", 1000), (1, "2026-11-03T02:00:00+08:00", 1400))  # 4 小時前那筆太近
    g = rank.observed_growth(obs(1, 1500), h, SCORE)
    assert g["delta"] == 500 and g["hours"] == 24.0


def test_three_day_gap_is_normalised_per_day():
    g = rank.observed_growth(obs(1, 1600), hist((1, "2026-10-31T06:00:00+08:00", 1000)), SCORE)
    assert g["hours"] == 72.0 and g["growth"] == 200


def test_gap_over_96_hours_falls_back_to_trending():
    h = hist((1, "2026-10-29T06:00:00+08:00", 1000))  # 120 小時前
    assert rank.observed_growth(obs(1, 1600), h, SCORE) is None
    main, *_ = rank.select(run([obs(1, 1600)], [hit(1, 1, 300)]), [], SCORE, h)
    assert main[0]["metric_method"] == "trending_daily"


def test_different_star_sources_are_not_compared():
    h = hist((1, "2026-11-02T06:00:00+08:00", 900, "search_api"))
    assert rank.observed_growth(obs(1, 1500), h, SCORE) is None


def test_reason_text_for_observed_growth():
    cand = {"obs": obs(1, 1600), "metric_method": "observed", "growth_per_day": 200.0, "score": 50.0,
            "trending_hits": [], "observed": {"delta": 600, "hours": 72.0, "base_at": "x"}}
    assert editorial.reasons(cand, BOARDS, "2026-11-03") == [
        "實際觀測：過去 72 小時增加 +600 星，換算每天約 +200 星", "今天不在 GitHub Trending 榜上，但持續成長"]


# ---- 驗收 2：星數下降 ----

def test_falling_stars_keep_negative_delta_and_rank_last():
    h = hist((1, "2026-11-02T06:00:00+08:00", 1200), (2, "2026-11-02T06:00:00+08:00", 1000))
    main, *_ = rank.select(run([obs(1, 1100), obs(2, 1300)]), [], SCORE, h)
    assert [c["obs"]["repo_id"] for c in main] == [2, 1]
    falling = main[1]
    assert falling["observed"]["delta"] == -100 and falling["growth_per_day"] == 0
    assert editorial.reasons(falling, BOARDS, "2026-11-03")[0] == "實際觀測：過去 24 小時減少 100 星"


# ---- 主榜收追蹤名單、新發現 30 天 ----

def test_tracked_repo_off_trending_can_enter_main():
    h = hist((7, "2026-11-02T06:00:00+08:00", 5000))
    main, *_ = rank.select(run([obs(7, 6000)]), [], SCORE, h)
    assert main[0]["obs"]["repo_id"] == 7 and main[0]["metric_method"] == "observed"


def test_discovery_uses_observed_growth_up_to_30_days():
    h = hist((1, "2026-11-02T06:00:00+08:00", 1000))
    observations = [obs(1, 2200, created="2026-10-15T00:00:00Z"),   # 19 天、觀測 +1200/天
                    obs(2, 9000, created="2026-10-15T00:00:00Z"),   # 19 天、沒有觀測 → 不收
                    obs(3, 300, created="2026-11-01T00:00:00Z")]    # 2 天、沒有觀測 → 建立以來平均
    _, discovery, *_ = rank.select(run(observations), [], SCORE, h)
    assert [(c["obs"]["repo_id"], c["metric_method"]) for c in discovery] == [(1, "observed"), (3, "lifetime_avg")]
    assert editorial.reasons(discovery[0], BOARDS, "2026-11-03")[1] == "建立 19 天，還沒上 GitHub Trending"


# ---- 驗收 3：冷卻與再次推薦 ----

def past(method="observed", growth=300.0, stars=5000):
    return [{"business_date": "2026-10-31", "edition_id": "2026-10-31",
             "picks": [{"repo_id": 1, "growth_per_day": growth, "metric_method": method, "stars": stars}]}]


def select_one(stars_now, past_editions):
    h = hist((1, "2026-11-02T06:00:00+08:00", stars_now - 700))
    return rank.select(run([obs(1, stars_now)]), past_editions, SCORE, h)


def test_burst_growth_is_recommended_again():
    main, _, excluded, _ = select_one(6000, past())  # 700/天 ≥ 2 × 300，且多了 1000 星
    assert excluded == [] and main[0]["repeat_of"] == "2026-10-31"
    assert "2.3 倍" in main[0]["repeat_reason"]


def test_no_repeat_when_growth_not_doubled_or_method_differs_or_last_zero():
    assert select_one(6000, past(growth=400.0))[2] == [1]            # 只有 1.75 倍
    assert select_one(6000, past(method="trending_daily"))[2] == [1]  # 數字種類不同
    assert select_one(6000, past(growth=0.0))[2] == [1]               # 上次沒有成長
    assert select_one(5050, past(stars=5000))[2] == [1]               # 只多了 50 星


# ---- 驗收 4：端到端、可重現、舊精選不受影響 ----

def test_edition_from_data_branch_uses_history_and_is_reproducible(tmp_path):
    data = tmp_path / "data"
    day1 = run([obs(1, 1000, "2026-11-02T06:00:00+08:00")], [hit(1, 1, 300)], date="2026-11-02")
    day2 = run([obs(1, 1800)], [hit(1, 1, 900)])
    for r in (day1, day2):
        store.write_json(data / store.run_path(r["business_date"], r["run_id"]), r)
    trig = {"event": "schedule", "schedule": "17 2 * * *", "scheduled_for": None, "delay_minutes": None}
    now = lambda: store.datetime.fromisoformat("2026-11-03T06:30:00+08:00")  # noqa: E731
    first = pipeline.maybe_build_edition(data, tmp_path / "a", "2026-11-03", None, trig, SCORE, BOARDS, now=now)
    again = pipeline.maybe_build_edition(data, tmp_path / "b", "2026-11-03", None, trig, SCORE, BOARDS, now=now)
    assert first == again and first["score_version"] == "d1"
    p = first["picks"][0]
    assert p["metric_method"] == "observed" and p["observed"]["delta"] == 800
    assert p["reasons"] == ["實際觀測：過去 24 小時增加 +800 星", "也在全站日榜第 1 名"]
    store.validate(first, "edition")


def test_old_b_editions_still_validate():
    old = {"slot": "main", "position": 1, "repo_id": 1, "full_name": "o/r1", "html_url": "https://github.com/o/r1",
           "description": None, "language": None, "topics": [], "license_spdx": None, "created_at": None,
           "pushed_at": None, "stars": 1, "forks": 0, "metric_method": "trending_daily", "growth_per_day": 1.0,
           "score": 1.0, "score_breakdown": None, "trending_hits": [], "reasons": ["x"], "notes": [],
           "repeat_of": None}
    edition = {"schema_version": "1", "score_version": "b1", "edition_id": "2026-10-28", "business_date": "2026-10-28",
               "generated_at": "x", "run_id": "x", "status": "normal",
               "coverage": {"sources_ok": [], "sources_failed": []}, "picks": [old], "cooldown_excluded": [],
               "candidate_counts": {"main": 1, "discovery": 0}}
    store.validate(edition, "edition")
