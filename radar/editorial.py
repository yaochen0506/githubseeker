"""入選理由與提醒：只用數字與規則組成，不靠 AI，也不猜測爆紅原因（計畫 §10.1）。"""

from datetime import date

from .rank import to_date


def age_days(created_at, business_date):
    created = to_date(created_at)
    return max((date.fromisoformat(business_date) - created).days, 1) if created else None


def _place(hit, boards):
    return f"{boards.get(hit['source_id'], hit['source_id'])}第 {hit['rank']} 名"


def reasons(candidate, boards, business_date):
    obs = candidate["obs"]
    if candidate["metric_method"] == "lifetime_avg":
        age = age_days(obs["created_at"], business_date)
        return [f"建立 {age} 天，已有 {obs['stars']:,} 星，建立以來平均每天 +{candidate['growth_per_day']:,.0f} 星"]

    period = "daily" if candidate["metric_method"] == "trending_daily" else "weekly"
    preferred = "all-daily" if period == "daily" else "all-weekly"
    leads = sorted((h for h in candidate["trending_hits"] if h["period"] == period and h["period_stars"] is not None),
                   key=lambda h: (h["source_id"] != preferred, h["rank"]))
    lead = leads[0]
    if period == "daily":
        text = [f"GitHub Trending 顯示今日 +{lead['period_stars']:,} 星（{_place(lead, boards)}）"]
    else:
        text = [f"GitHub Trending 顯示本週 +{lead['period_stars']:,} 星（{_place(lead, boards)}），"
                f"平均每天約 +{candidate['growth_per_day']:,.0f} 星"]

    others = sorted((h for h in candidate["trending_hits"] if h is not lead),
                    key=lambda h: (h["period"] != "daily", h["rank"]))
    if others:
        listed = "、".join(_place(h, boards) for h in others[:3])
        more = f" 等 {len(others)} 個榜單" if len(others) > 3 else ""
        space = " " if listed[0].isascii() else ""  # 中文接英文時加半形空格
        text.append(f"也在{space}{listed}{more}")
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
