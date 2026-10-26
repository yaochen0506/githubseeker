"""資料來源：GitHub Trending 網頁、Search API、repository API。

只負責「拿資料」與「判斷來源是否健康」，不做評分，也不寫檔。
"""

import json
import re
import time

import requests
from bs4 import BeautifulSoup

API_ROOT = "https://api.github.com"
WEB_ROOT = "https://github.com"

NAME_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
PERIOD_STARS_RE = re.compile(r"([\d,]+)\s+stars?\s+(today|this week|this month)", re.I)
PERIOD_WORDS = {"today": "daily", "this week": "weekly", "this month": "monthly"}


class NotFound(Exception):
    pass


class RateLimited(Exception):
    pass


class BudgetExhausted(Exception):
    pass


class ApiError(Exception):
    pass


# ---------- Trending 網頁 ----------

def trending_url(path, period, spoken_language=None):
    url = f"{WEB_ROOT}{path}?since={period}"
    if spoken_language:
        url += f"&spoken_language_code={spoken_language}"
    return url


def _to_int(text):
    digits = text.replace(",", "").strip()
    return int(digits) if digits.isdigit() else None


def _record_id(row, link):
    """頁面的點擊追蹤資料裡附有專案編號；這不是公開保證的格式，取不到就回傳 None。"""
    candidates = [link] + row.select("[data-hydro-click]")
    for el in candidates:
        raw = el.get("data-hydro-click")
        if not raw:
            continue
        try:
            payload = json.loads(raw).get("payload", {})
        except (ValueError, AttributeError):
            continue
        for key in ("record_id", "repository_id"):
            value = payload.get(key)
            if isinstance(value, int) and value > 0:
                return value
    return None


def parse_trending(html, period):
    """解析 Trending 頁面。期間新增星數和請求的榜別對不上時存 None，不填 0。"""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for rank, row in enumerate(soup.select("article.Box-row"), 1):
        link = row.select_one("h2 a[href]")
        if not link:
            continue
        full_name = link["href"].strip("/")
        if not NAME_RE.match(full_name):
            continue

        period_stars = None
        match = PERIOD_STARS_RE.search(" ".join(row.get_text(" ").split()))
        if match and PERIOD_WORDS[match.group(2).lower()] == period:
            period_stars = _to_int(match.group(1))

        stars_link = row.select_one('a[href$="/stargazers"]')
        rows.append({
            "rank": rank,
            "repo_id": _record_id(row, link),
            "full_name": full_name,
            "period_stars": period_stars,
            "page_stars": _to_int(stars_link.get_text()) if stars_link else None,
        })
    return rows


def fetch_trending(session, url, period, user_agent, min_rows, sleep=time.sleep):
    """抓一頁 Trending。回傳 (來源紀錄, 列)；失敗時列為空清單。"""
    source = {"url": url, "period": period, "status": "failed", "http_status": None, "rows": 0, "error": None}
    for attempt in range(3):
        try:
            resp = session.get(url, headers={"User-Agent": user_agent, "Accept": "text/html"}, timeout=20)
        except requests.RequestException as exc:
            source["error"] = f"network: {exc.__class__.__name__}"
            sleep(5 * (attempt + 1))
            continue

        source["http_status"] = resp.status_code
        if resp.status_code == 200:
            rows = parse_trending(resp.text, period)
            source["rows"] = len(rows)
            if len(rows) < min_rows:
                # 回 200 卻幾乎沒有資料，多半是版型改了，不能當成「今天沒有熱門專案」
                source["error"] = f"too_few_rows: {len(rows)} < {min_rows}"
                return source, []
            source["status"], source["error"] = "ok", None
            return source, rows

        source["error"] = f"http_{resp.status_code}"
        if resp.status_code == 429 or resp.status_code >= 500:
            sleep(min(_retry_after(resp) or 10 * (attempt + 1), 60))
            continue
        return source, []
    return source, []


def _retry_after(resp):
    value = resp.headers.get("retry-after", "")
    return int(value) if value.isdigit() else None


# ---------- GitHub REST API ----------

class GitHubApi:
    """包一層 REST 呼叫：計算用量、處理限流、守住每次執行的請求上限。"""

    def __init__(self, session, token=None, max_requests=450, max_wait=90, sleep=time.sleep, clock=time.time):
        self.session = session
        self.max_requests = max_requests
        self.max_wait = max_wait
        self.sleep = sleep
        self.clock = clock
        self.requests = 0
        self.usage = {}
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, path, params=None):
        for attempt in range(3):
            if self.requests >= self.max_requests:
                raise BudgetExhausted(f"{self.requests} requests")
            self.requests += 1
            try:
                resp = self.session.get(API_ROOT + path, params=params, headers=self.headers, timeout=20)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise ApiError(f"network: {exc.__class__.__name__}") from exc
                self.sleep(2 ** attempt)
                continue
            self._track(resp)

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (404, 410, 451):
                raise NotFound(f"http_{resp.status_code}")
            wait = self._rate_limit_wait(resp)
            if wait is not None:
                if wait > self.max_wait or attempt == 2:
                    raise RateLimited(f"http_{resp.status_code}, wait {wait:.0f}s")
                self.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt < 2:
                self.sleep(2 ** attempt)
                continue
            raise ApiError(f"http_{resp.status_code}")
        raise ApiError("retries exhausted")

    def _rate_limit_wait(self, resp):
        """403／429 是否為限流；是的話回傳要等的秒數，不是就回傳 None。"""
        if resp.status_code not in (403, 429):
            return None
        retry_after = _retry_after(resp)
        if retry_after is not None:
            return retry_after
        if resp.headers.get("x-ratelimit-remaining") == "0":
            reset = resp.headers.get("x-ratelimit-reset", "")
            if reset.isdigit():
                return max(0.0, int(reset) - self.clock()) + 1
            return 60
        return 60 if resp.status_code == 429 else None

    def _track(self, resp):
        resource = resp.headers.get("x-ratelimit-resource", "unknown")
        entry = self.usage.setdefault(resource, {
            "requests": 0, "limit": None, "remaining_first": None, "remaining_last": None,
            "reset_first": None, "reset_last": None,
        })
        entry["requests"] += 1
        remaining = resp.headers.get("x-ratelimit-remaining", "")
        reset = resp.headers.get("x-ratelimit-reset", "")
        limit = resp.headers.get("x-ratelimit-limit", "")
        if remaining.isdigit():
            if entry["remaining_first"] is None:
                entry["remaining_first"], entry["reset_first"] = int(remaining), reset
            entry["remaining_last"], entry["reset_last"] = int(remaining), reset
        if limit.isdigit():
            entry["limit"] = int(limit)

    def usage_report(self):
        """用 x-ratelimit-remaining 的前後差值算實際用量；配額中途重置就無法比較，給 None。"""
        report = {}
        for resource, e in self.usage.items():
            used = None
            if e["remaining_first"] is not None and e["reset_first"] == e["reset_last"]:
                used = e["remaining_first"] - e["remaining_last"] + 1
            report[resource] = {
                "requests": e["requests"], "limit": e["limit"],
                "remaining_first": e["remaining_first"], "remaining_last": e["remaining_last"],
                "used_by_headers": used,
            }
        return report


def search_query(created_since, min_stars):
    return f"created:>={created_since} stars:>={min_stars} fork:false archived:false"


def search_repositories(api, query):
    """Search 只取第 1 頁。回傳 (來源紀錄, 專案清單)。"""
    url = f"{API_ROOT}/search/repositories"
    source = {"url": url, "query": query, "status": "failed", "http_status": None, "rows": 0,
              "total_count": None, "incomplete_results": None, "error": None}
    try:
        data = api.get("/search/repositories", params={"q": query, "sort": "stars", "order": "desc", "per_page": 100})
    except (RateLimited, BudgetExhausted, ApiError, NotFound) as exc:
        source["error"] = f"{exc.__class__.__name__}: {exc}"
        return source, []
    items = data.get("items", [])
    source.update(status="ok", http_status=200, rows=len(items),
                  total_count=data.get("total_count"), incomplete_results=data.get("incomplete_results"))
    return source, items


def repo_snapshot(item, observed_at, stars_source):
    """把 API 回傳的專案資料整理成觀測紀錄（不含 README）。"""
    license_info = item.get("license") or {}
    description = item.get("description")
    return {
        "repo_id": item["id"],
        "full_name": item["full_name"],
        "observed_at": observed_at,
        "stars": item["stargazers_count"],
        "forks": item["forks_count"],
        "open_issues": item.get("open_issues_count"),
        "stars_source": stars_source,
        "origins": [],
        "description": description[:500] if description else None,
        "language": item.get("language"),
        "topics": item.get("topics") or [],
        "license_spdx": license_info.get("spdx_id"),
        "homepage": item.get("homepage") or None,
        "created_at": item.get("created_at"),
        "pushed_at": item.get("pushed_at"),
        "archived": item.get("archived"),
        "fork": item.get("fork"),
        "renamed_from": None,
    }
