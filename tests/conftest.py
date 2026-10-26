"""測試用的假網路：不連網，依網址回傳預先排好的回應。"""

import html
import json
from datetime import datetime

import pytest

from radar.sources import API_ROOT
from radar.store import TAIPEI


class FakeResponse:
    def __init__(self, status=200, body=None, text="", headers=None):
        self.status_code = status
        self._body = body
        self.text = text
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def json(self):
        return self._body


class FakeSession:
    """routes：網址 → 回應或回應清單（依序回傳，最後一個重複使用）。"""

    def __init__(self, routes):
        self.routes = {k: (v if isinstance(v, list) else [v]) for k, v in routes.items()}
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if url not in self.routes:
            raise AssertionError(f"未預期的請求：{url}")
        queue = self.routes[url]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def api(path):
    return API_ROOT + path


def row_html(full_name, repo_id=None, period_text="12 stars today", stars="1,234"):
    """仿照 2026-10-26 實際 Trending 頁面的一列結構（只保留解析需要的部分）。"""
    hydro = ""
    if repo_id:
        payload = {"event_type": "explore.click", "payload": {"click_target": "REPOSITORY", "record_id": repo_id}}
        hydro = f' data-hydro-click="{html.escape(json.dumps(payload))}"'
    owner, name = full_name.split("/")
    period = f'<span class="d-inline-block float-sm-right">{period_text}</span>' if period_text else ""
    return f"""
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a{hydro} href="/{full_name}" class="Link"><span class="text-normal">{owner} /</span> {name}</a>
  </h2>
  <p class="col-9 color-fg-muted my-1">A <b>test</b> repo</p>
  <div class="f6 color-fg-muted mt-2">
    <span itemprop="programmingLanguage">Python</span>
    <a href="/{full_name}/stargazers" class="Link Link--muted d-inline-block">{stars}</a>
    <a href="/{full_name}/forks" class="Link Link--muted d-inline-block">56</a>
    {period}
  </div>
</article>"""


def page_html(*rows):
    return "<html><body><div class='Box'>" + "".join(rows) + "</div></body></html>"


def repo_json(repo_id, full_name, stars=100):
    return {
        "id": repo_id, "full_name": full_name, "stargazers_count": stars, "forks_count": 3,
        "open_issues_count": 1, "description": "desc", "language": "Python", "topics": ["ai"],
        "license": {"spdx_id": "MIT"}, "homepage": "", "created_at": "2026-10-01T00:00:00Z",
        "pushed_at": "2026-10-25T00:00:00Z", "archived": False, "fork": False,
    }


def search_json(*items):
    return {"total_count": len(items), "incomplete_results": False, "items": list(items)}


def config(trending=None, search=None):
    return {
        "user_agent": "test-agent", "min_rows": 1, "page_delay_seconds": 0,
        "max_rest_requests": 100, "max_rate_wait_seconds": 30,
        "trending": trending or [
            {"id": "all-daily", "path": "/trending", "period": "daily"},
            {"id": "all-weekly", "path": "/trending", "period": "weekly"},
        ],
        "search": search if search is not None else [{"id": "new-30d", "created_within_days": 30, "min_stars": 50}],
        "tracking": {"max_size": 200, "max_age_days": 14, "search_top": 25},
    }


SEARCH_URL = api("/search/repositories")
DAILY_URL = "https://github.com/trending?since=daily"
WEEKLY_URL = "https://github.com/trending?since=weekly"


@pytest.fixture
def started():
    return datetime(2026, 10, 26, 6, 40, 0, tzinfo=TAIPEI)
