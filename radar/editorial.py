"""入選理由與提醒：只用數字與規則組成，不靠 AI，也不猜測爆紅原因（計畫 §10.1）。"""

from datetime import date

from .rank import to_date


def age_days(created_at, business_date):
    created = to_date(created_at)
    return max((date.fromisoformat(business_date) - created).days, 1) if created else None


def _place(hit, boards):
    return f"{boards.get(hit['source_id'], hit['source_id'])}第 {hit['rank']} 名"


def _also_on(hits, boards, exclude=None):
    others = sorted((h for h in hits if h is not exclude), key=lambda h: (h["period"] != "daily", h["rank"]))
    if not others:
        return None
    listed = "、".join(_place(h, boards) for h in others[:3])
    more = f" 等 {len(others)} 個榜單" if len(others) > 3 else ""
    space = " " if listed[0].isascii() else ""  # 中文接英文時加半形空格
    return f"也在{space}{listed}{more}"


def _observed_text(observed, growth):
    hours, delta = observed["hours"], observed["delta"]
    change = f"增加 +{delta:,} 星" if delta >= 0 else f"減少 {-delta:,} 星"
    text = f"實際觀測：過去 {hours:.0f} 小時{change}"
    if abs(hours - 24) > 3:
        text += f"，換算每天約 +{growth:,.0f} 星"
    return text


def reasons(candidate, boards, business_date):
    obs, method, hits = candidate["obs"], candidate["metric_method"], candidate["trending_hits"]
    if method == "lifetime_avg":
        age = age_days(obs["created_at"], business_date)
        text = [f"建立 {age} 天，已有 {obs['stars']:,} 星，建立以來平均每天 +{candidate['growth_per_day']:,.0f} 星"]
    elif method == "observed":
        text = [_observed_text(candidate["observed"], candidate["growth_per_day"])]
        also = _also_on(hits, boards)
        if also:
            text.append(also)
        elif candidate["score"] is not None:
            text.append("今天不在 GitHub Trending 榜上，但持續成長")
        else:
            text.append(f"建立 {age_days(obs['created_at'], business_date)} 天，還沒上 GitHub Trending")
    else:
        period = "daily" if method == "trending_daily" else "weekly"
        preferred = "all-daily" if period == "daily" else "all-weekly"
        lead = sorted((h for h in hits if h["period"] == period and h["period_stars"] is not None),
                      key=lambda h: (h["source_id"] != preferred, h["rank"]))[0]
        if period == "daily":
            text = [f"GitHub Trending 顯示今日 +{lead['period_stars']:,} 星（{_place(lead, boards)}）"]
        else:
            text = [f"GitHub Trending 顯示本週 +{lead['period_stars']:,} 星（{_place(lead, boards)}），"
                    f"平均每天約 +{candidate['growth_per_day']:,.0f} 星"]
        also = _also_on(hits, boards, exclude=lead)
        if also:
            text.append(also)
    if candidate.get("repeat_reason"):
        text.append(candidate["repeat_reason"])
    return text


def notes(obs, business_date):
    """給非工程師的提醒：授權與專案年齡。"""
    out = []
    if obs.get("license_spdx") is None:
        out.append("未標示授權：使用、修改或轉載前，請先確認作者是否允許")
    elif obs["license_spdx"] == "NOASSERTION":
        out.append("授權類型無法自動判別，使用前請到專案頁確認")
    age = age_days(obs.get("created_at"), business_date)
    if age is not None and age <= 7:
        out.append(f"建立僅 {age} 天的新專案，功能與安全性還沒經過時間考驗")
    return out
