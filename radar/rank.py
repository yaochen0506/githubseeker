"""每日精選的選法：資格、每日成長、分數、名額、冷卻（稽核報告 §5）。

每日成長依序取：實際觀測（D 階段）→ Trending 日榜 → Trending 週榜 ÷ 7；
新發現沒有觀測時，剛建立的專案用「建立以來平均」。
"""

import math
from datetime import date, datetime


def to_date(iso_text):
    if not iso_text:
        return None
    return datetime.fromisoformat(iso_text.replace("Z", "+00:00")).date()


def eligible(obs, business_date, cfg):
    """最近有更新、沒封存、不是 fork。資料缺漏時一律不列入，不猜。"""
    pushed = to_date(obs.get("pushed_at"))
    return (obs.get("archived") is False and obs.get("fork") is False and pushed is not None
            and (business_date - pushed).days <= cfg["active_within_days"])


def trending_hits_by_repo(run):
    """只採用成功來源的 Trending 命中，並附上榜別（日／週）。"""
    periods = {s["source_id"]: s["period"] for s in run["sources"]
               if s["kind"] == "trending" and s["status"] == "ok"}
    by_repo = {}
    for hit in run["source_hits"]:
        if hit["source_id"] in periods and hit["repo_id"]:
            by_repo.setdefault(hit["repo_id"], []).append({
                "source_id": hit["source_id"], "rank": hit["rank"],
                "period": periods[hit["source_id"]], "period_stars": hit["period_stars"],
            })
    return by_repo


def daily_growth(hits):
    """每個專案只取一個每日成長數字：日榜優先，只有週榜時用週榜 ÷ 7。"""
    daily = [h["period_stars"] for h in hits if h["period"] == "daily" and h["period_stars"] is not None]
    if daily:
        return float(max(daily)), "trending_daily"
    weekly = [h["period_stars"] for h in hits if h["period"] == "weekly" and h["period_stars"] is not None]
    if weekly:
        return max(weekly) / 7, "trending_weekly_avg"
    return None, None


def score(growth, stars, cfg):
    g = max(growth, 0.0)
    base = max(stars - g, 0)
    s_abs = min(1.0, math.log10(1 + g) / math.log10(1 + cfg["abs_full_at"]))
    s_rel = min(1.0, g / max(base, cfg["rel_base_floor"]) / cfg["rel_full_at"])
    total = round(100 * (cfg["weight_abs"] * s_abs + cfg["weight_rel"] * s_rel), 1)
    return total, {"s_abs": round(s_abs, 4), "s_rel": round(s_rel, 4)}


def best_rank(hits):
    """同分時的排序依據：日榜名次優先於週榜名次；不在 Trending 的排最後。"""
    if not hits:
        return (2, 0)
    return min(((h["period"] != "daily"), h["rank"]) for h in hits)


def observed_growth(obs, history, cfg):
    """和 12～96 小時前的觀測比較，換算每天增加幾星。只比較同一種來源的數字；沒有合適的基準就回傳 None。

    history：repo_id → [(observed_at, stars, stars_source), ...]
    """
    now = datetime.fromisoformat(obs["observed_at"])
    earlier = sorted((datetime.fromisoformat(t), s, src) for t, s, src in history.get(obs["repo_id"], []))
    for at, stars, source in reversed(earlier):
        hours = (now - at).total_seconds() / 3600
        if hours < cfg["observed_min_hours"] or source != obs["stars_source"]:
            continue
        if hours > cfg["observed_max_hours"]:
            return None
        delta = obs["stars"] - stars
        return {"growth": delta * 24 / hours, "delta": delta, "hours": round(hours, 1), "base_at": at.isoformat()}
    return None


def method_class(method):
    return {"observed": "observed", "trending_daily": "trending", "trending_weekly_avg": "trending"}.get(method, method)


def last_picks(past_editions, business_date, days):
    """冷卻期間內每個專案最近一次入選的紀錄。"""
    found = {}
    for edition in sorted(past_editions, key=lambda e: e["business_date"]):
        gap = (business_date - date.fromisoformat(edition["business_date"])).days
        if 0 < gap <= days:
            for p in edition["picks"]:
                found[p["repo_id"]] = {"edition_id": edition["edition_id"], "growth": p["growth_per_day"],
                                       "method": p["metric_method"], "stars": p["stars"]}
    return found


def cooldown_ids(past_editions, business_date, days):
    return set(last_picks(past_editions, business_date, days))


def repeat_check(last, method, growth, stars, cfg):
    """7 天冷卻的例外：同一類數字、上次有成長、這次成長達倍數、累積新增夠多。回傳原因文字或 None。"""
    if method_class(last["method"]) != method_class(method) or last["growth"] <= 0:
        return None
    if growth < cfg["repeat_growth_multiple"] * last["growth"] or stars - last["stars"] < cfg["repeat_min_new_stars"]:
        return None
    return (f"再次推薦：每天成長是上次入選（{last['edition_id']}）的 {growth / last['growth']:.1f} 倍，"
            f"之後已增加 {stars - last['stars']:,} 星")


def build_history(runs):
    """近幾次收集的觀測：repo_id → [(observed_at, stars, stars_source), ...]。"""
    history = {}
    for run in runs:
        for o in run["observations"]:
            history.setdefault(o["repo_id"], []).append((o["observed_at"], o["stars"], o["stars_source"]))
    return history


def seen_counts(runs, current_run_id):
    """排除本次之外，每個專案被觀測幾次、上過 Trending 幾次：repo_id → (觀測次數, Trending 次數)。"""
    counts = {}
    for run in runs:
        if run["run_id"] == current_run_id:
            continue
        on_trending = set(trending_hits_by_repo(run))
        for o in run["observations"]:
            seen, trending = counts.get(o["repo_id"], (0, 0))
            counts[o["repo_id"]] = (seen + 1, trending + (o["repo_id"] in on_trending))
    return counts


def trending_floor(run):
    """今天全站日榜上新增最少的那個專案的數字，當作「上 Trending 的門檻」；抓不到就回傳 None。"""
    values = [h["period_stars"] for h in run["source_hits"]
              if h["source_id"] == "all-daily" and h["period_stars"] is not None]
    ok = any(s["source_id"] == "all-daily" and s["status"] == "ok" for s in run["sources"])
    return min(values) if ok and values else None


def candidates(run, past_editions, cfg, history=None, seen=None, categories=None):
    """所有候選，已排序。回傳 (主榜池, 新發現池, 冷卻排除, 疑似異常排除, 不收錄)。"""
    business_date = date.fromisoformat(run["business_date"])
    hits_by_repo = trending_hits_by_repo(run)
    recent = last_picks(past_editions, business_date, cfg["cooldown_days"])
    history, seen, categories = history or {}, seen or {}, categories or {}
    floor = trending_floor(run)
    main, discovery, excluded, suspicious, blocked = [], [], set(), set(), set()

    for obs in run["observations"]:
        if not eligible(obs, business_date, cfg):
            continue
        repo_id = obs["repo_id"]
        category = (categories.get(repo_id) or {}).get("category")
        if category == "excluded":
            blocked.add(repo_id)
            continue
        hits = hits_by_repo.get(repo_id, [])
        observed = observed_growth(obs, history, cfg)
        # 成長遠超過上 Trending 的門檻、追蹤多次卻從沒上過 Trending：GitHub 可能刻意排除，先不推薦
        times_seen, times_trending = seen.get(repo_id, (0, 0))
        if (observed and not hits and floor and times_seen >= cfg["suspicious_min_runs"] and times_trending == 0
                and observed["growth"] > cfg["suspicious_growth_multiple"] * floor):
            suspicious.add(repo_id)
            continue
        created = to_date(obs.get("created_at"))
        age = max((business_date - created).days, 1) if created else None

        if (not hits and age is not None and age <= cfg["discovery_max_age_days"]
                and obs["stars"] >= cfg["discovery_min_stars"]):
            # 新發現：有觀測就看實際成長；第一次出現的只收剛建立的，用建立以來平均
            if observed:
                growth, method = max(observed["growth"], 0.0), "observed"
            elif age <= cfg["discovery_new_max_age_days"]:
                growth, method = obs["stars"] / age, "lifetime_avg"
            else:
                continue
            pool, total, breakdown = discovery, None, None
        else:
            if observed:
                growth, method = max(observed["growth"], 0.0), "observed"
            else:
                growth, method = daily_growth(hits)
            if growth is None:
                continue
            pool = main
            total, breakdown = score(growth, obs["stars"], cfg)

        repeat_of = repeat_reason = None
        if repo_id in recent:
            repeat_reason = repeat_check(recent[repo_id], method, growth, obs["stars"], cfg)
            if repeat_reason is None:
                excluded.add(repo_id)
                continue
            repeat_of = recent[repo_id]["edition_id"]
        pool.append({"obs": obs, "metric_method": method, "growth_per_day": growth, "score": total,
                     "score_breakdown": breakdown, "trending_hits": hits, "observed": observed,
                     "repeat_of": repeat_of, "repeat_reason": repeat_reason, "category": category,
                     # 分類榜不分主榜或新發現，一律用同一種分數比較
                     "rank_score": total if total is not None else score(growth, obs["stars"], cfg)[0]})

    # 同分時，每天新增星數多的優先，再看 Trending 名次
    main.sort(key=lambda c: (-c["score"], -c["growth_per_day"], best_rank(c["trending_hits"]), c["obs"]["repo_id"]))
    discovery.sort(key=lambda c: (-c["growth_per_day"], c["obs"]["repo_id"]))
    return main, discovery, sorted(excluded), sorted(suspicious), sorted(blocked)


def select(run, past_editions, cfg, history=None, seen=None, categories=None):
    """回傳 (主榜, 新發現, 因冷卻排除的編號, 各池候選數)。"""
    main, discovery, excluded, _, _ = candidates(run, past_editions, cfg, history, seen, categories)
    counts = {"main": len(main), "discovery": len(discovery)}
    return main[:cfg["max_main"]], discovery[:cfg["max_discovery"]], excluded, counts


def category_boards(main_pool, discovery_pool, used_ids, category_ids, per_category):
    """每個分類取分數最高的幾個；同一天已經入選（主榜、新發現或其他分類）的專案不重複。"""
    used = set(used_ids)
    ranked = sorted(main_pool + discovery_pool, key=lambda c: (-c["rank_score"], -c["growth_per_day"],
                                                               c["obs"]["repo_id"]))
    boards = {}
    for cat in category_ids:
        board = [c for c in ranked if c["category"] == cat and c["obs"]["repo_id"] not in used][:per_category]
        used |= {c["obs"]["repo_id"] for c in board}
        boards[cat] = board
    return boards
