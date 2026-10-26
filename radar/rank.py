"""每日精選的選法：資格、每日成長、分數、名額、冷卻（稽核報告 §5）。

B 階段只用 Trending 的數字（metric 2、3）與「建立以來平均」；依實際觀測計算的成長在 D 階段加入。
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
    """同分時的排序依據：日榜名次優先於週榜名次。"""
    return min(((h["period"] != "daily"), h["rank"]) for h in hits)


def cooldown_ids(past_editions, business_date, days):
    ids = set()
    for edition in past_editions:
        gap = (business_date - date.fromisoformat(edition["business_date"])).days
        if 0 < gap <= days:
            ids |= {p["repo_id"] for p in edition["picks"]}
    return ids


def select(run, past_editions, cfg):
    """回傳 (主榜, 新發現, 因冷卻排除的編號, 各池候選數)。"""
    business_date = date.fromisoformat(run["business_date"])
    hits_by_repo = trending_hits_by_repo(run)
    cooled = cooldown_ids(past_editions, business_date, cfg["cooldown_days"])
    main, discovery, excluded = [], [], set()

    for obs in run["observations"]:
        if not eligible(obs, business_date, cfg):
            continue
        repo_id = obs["repo_id"]
        hits = hits_by_repo.get(repo_id, [])
        if hits:
            growth, method = daily_growth(hits)
            if growth is None:
                continue
            if repo_id in cooled:
                excluded.add(repo_id)
                continue
            total, breakdown = score(growth, obs["stars"], cfg)
            main.append({"obs": obs, "metric_method": method, "growth_per_day": growth,
                         "score": total, "score_breakdown": breakdown, "trending_hits": hits})
        else:
            created = to_date(obs.get("created_at"))
            if created is None:
                continue
            age = max((business_date - created).days, 1)
            if age > cfg["discovery_max_age_days"] or obs["stars"] < cfg["discovery_min_stars"]:
                continue
            if repo_id in cooled:
                excluded.add(repo_id)
                continue
            discovery.append({"obs": obs, "metric_method": "lifetime_avg", "growth_per_day": obs["stars"] / age,
                              "score": None, "score_breakdown": None, "trending_hits": []})

    # 同分時，每天新增星數多的優先，再看 Trending 名次
    main.sort(key=lambda c: (-c["score"], -c["growth_per_day"], best_rank(c["trending_hits"]), c["obs"]["repo_id"]))
    discovery.sort(key=lambda c: (-c["growth_per_day"], c["obs"]["repo_id"]))
    counts = {"main": len(main), "discovery": len(discovery)}
    return main[:cfg["max_main"]], discovery[:cfg["max_discovery"]], sorted(excluded), counts
