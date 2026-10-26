"""A 階段流程：收集 Trending 與 Search、補齊專案資料、觀測追蹤名單，寫出 run 檔。

用法：
  python -m radar collect --data-dir data --out-dir out [--force] [--max-rest N]
  python -m radar validate out

collect 只讀 data-dir（資料分支的內容），新產生的檔案全部寫到 out-dir，
由 workflow 的另一個 job 驗證後再提交到資料分支。
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
import tomllib
from datetime import date, timedelta
from pathlib import Path

import requests

from . import sources, store
from .sources import ApiError, BudgetExhausted, GitHubApi, NotFound, RateLimited

ROOT = Path(__file__).resolve().parent.parent


def load_config(path):
    raw = Path(path).read_bytes()
    return tomllib.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def trigger_info(started, event, schedule):
    """記錄預定觸發時間與實際延遲，累積後用來調整排程時間。cron 以台北時間解讀。"""
    info = {"event": event, "schedule": schedule or None, "scheduled_for": None, "delay_minutes": None}
    match = re.fullmatch(r"(\d{1,2}) (\d{1,2}) \* \* \*", (schedule or "").strip())
    if match:
        slot = started.replace(hour=int(match.group(2)), minute=int(match.group(1)), second=0)
        if slot > started:
            slot -= timedelta(days=1)
        info["scheduled_for"] = store.iso(slot)
        info["delay_minutes"] = round((started - slot).total_seconds() / 60, 1)
    return info


class Collector:
    def __init__(self, config, api, session, sleep=time.sleep, now=store.now_taipei):
        self.config = config
        self.api = api
        self.session = session
        self.sleep = sleep
        self.now = now
        self.sources = []
        self.hits = []
        self.observations = {}
        self.unresolved = []
        self.gone_ids = set()
        self.api_stop = None  # 限流或用完預算後，剩下的專案直接記為未解析，不再浪費請求

    # ---- 收集 ----

    def collect_trending(self):
        cfg = self.config
        for i, page in enumerate(cfg["trending"]):
            if i:
                self.sleep(cfg["page_delay_seconds"])
            url = sources.trending_url(page["path"], page["period"], page.get("spoken_language"))
            src, rows = sources.fetch_trending(self.session, url, page["period"], cfg["user_agent"],
                                               cfg["min_rows"], self.sleep)
            self.sources.append({"source_id": page["id"], "kind": "trending", **src})
            self.hits += [{"source_id": page["id"], **row} for row in rows]

    def collect_search(self, business_date):
        results = []
        for q in self.config["search"]:
            since = (date.fromisoformat(business_date) - timedelta(days=q["created_within_days"])).isoformat()
            src, items = sources.search_repositories(self.api, sources.search_query(since, q["min_stars"]))
            self.sources.append({"source_id": q["id"], "kind": "search", **src})
            for rank, item in enumerate(items, 1):
                self.hits.append({"source_id": q["id"], "rank": rank, "repo_id": item["id"],
                                  "full_name": item["full_name"], "period_stars": None, "page_stars": None})
                results.append(item)
        return results

    # ---- 補齊專案資料 ----

    def observe(self, repo_id, full_name, fallback_item=None, from_tracking=False):
        """用 repository API 取得最新資料。成功回傳觀測紀錄，失敗記到 unresolved 並回傳 None。"""
        if repo_id and repo_id in self.observations:
            return self.observations[repo_id]
        reason, detail = self.api_stop, None
        item = None
        if reason is None:
            try:
                item = self._fetch(repo_id, full_name)
            except NotFound as exc:
                reason, detail = "not_found", str(exc)
                if from_tracking and repo_id:
                    self.gone_ids.add(repo_id)
            except (RateLimited, BudgetExhausted) as exc:
                reason = "rate_limited" if isinstance(exc, RateLimited) else "budget_exhausted"
                detail, self.api_stop = str(exc), reason
            except ApiError as exc:
                reason, detail = "error", str(exc)

        if item is not None:
            obs = sources.repo_snapshot(item, store.iso(self.now()), "repo_api")
            if full_name and full_name.lower() != item["full_name"].lower():
                obs["renamed_from"] = full_name
        elif fallback_item is not None:
            # 配額不夠時退回 Search 的資料；標記來源，成長計算時不和 repo_api 的數字混用
            obs = sources.repo_snapshot(fallback_item, store.iso(self.now()), "search_api")
        else:
            self.unresolved.append({"repo_id": repo_id, "full_name": full_name, "reason": reason, "detail": detail})
            return None
        return self.observations.setdefault(obs["repo_id"], obs)

    def _fetch(self, repo_id, full_name):
        if not repo_id:
            return self.api.get(f"/repos/{full_name}")  # 改名的專案會自動轉址到新名稱
        item = self.api.get(f"/repositories/{repo_id}")
        if full_name and item["full_name"].lower() != full_name.lower():
            # 名稱對不上：可能是改名，也可能是頁面上的編號取錯，用名稱再查一次確認
            try:
                by_name = self.api.get(f"/repos/{full_name}")
            except NotFound:
                return item
            if by_name["id"] != repo_id:
                return by_name
        return item

    # ---- 主流程 ----

    def run(self, business_date, tracking):
        self.collect_trending()
        search_items = self.collect_search(business_date)

        # 1. Trending 上的專案（同一專案出現在多個榜，只查一次）
        trending_sources = {s["source_id"] for s in self.sources if s["kind"] == "trending"}
        trending_hits = [h for h in self.hits if h["source_id"] in trending_sources]
        id_by_name = {h["full_name"].lower(): h["repo_id"] for h in trending_hits if h["repo_id"]}
        for hit in trending_hits:
            hit["repo_id"] = hit["repo_id"] or id_by_name.get(hit["full_name"].lower())

        resolved = {}
        for hit in trending_hits:
            key = hit["repo_id"] or hit["full_name"].lower()
            if key not in resolved:
                obs = self.observe(hit["repo_id"], hit["full_name"])
                resolved[key] = obs["repo_id"] if obs else None
            hit["repo_id"] = resolved[key] or hit["repo_id"]
        trending_ids = {v for v in resolved.values() if v}

        # 2. Search 找到的新專案：排除已在 Trending 的，取前幾名
        search_new, picked = [], set(trending_ids)
        for item in search_items:
            if len(search_new) >= self.config["tracking"]["search_top"]:
                break
            if item["id"] not in picked:
                picked.add(item["id"])
                search_new.append(item)
        for item in search_new:
            self.observe(item["id"], item["full_name"], fallback_item=item)

        # 3. 追蹤名單：不管 Trending 今天有沒有成功，都照常觀測
        tracked_ids = {e["repo_id"] for e in tracking}
        for entry in tracking:
            self.observe(entry["repo_id"], entry["full_name"], from_tracking=True)

        # 標記每筆觀測的來由
        search_ids = {i["id"] for i in search_items}
        for obs in self.observations.values():
            origins = []
            if obs["repo_id"] in trending_ids:
                origins.append("trending")
            if obs["repo_id"] in search_ids:
                origins.append("search")
            if obs["repo_id"] in tracked_ids:
                origins.append("tracking")
            obs["origins"] = origins or ["tracking"]

        seen = [(rid, self.observations[rid]["full_name"]) for rid in trending_ids]
        seen += [(i["id"], self.observations[i["id"]]["full_name"]) for i in search_new if i["id"] in self.observations]
        return seen


def collect(config, config_sha, data_dir, out_dir, api, session, started, trigger, code_version, run_suffix,
            force=False, sleep=time.sleep, now=store.now_taipei):
    """執行一次收集。今天已有完整的 run 且沒有 force 時回傳 None（不產生任何檔案）。"""
    business_date = started.date().isoformat()
    if not force and store.complete_run_exists(data_dir, business_date):
        return None

    tracking = store.load_tracking(data_dir)
    collector = Collector(config, api, session, sleep=sleep, now=now)
    seen = collector.run(business_date, tracking)

    blocking = {"rate_limited", "budget_exhausted", "error"}
    complete = (all(s["status"] == "ok" for s in collector.sources)
                and not any(u["reason"] in blocking for u in collector.unresolved))
    run_id = f"{started:%Y%m%dT%H%M%S}-{run_suffix}"
    finished = now()
    run_doc = {
        "schema_version": "1",
        "run_id": run_id,
        "business_date": business_date,
        "started_at": store.iso(started),
        "finished_at": store.iso(finished),
        "trigger": trigger,
        "code_version": code_version,
        "config_sha256": config_sha,
        "complete": complete,
        "sources": collector.sources,
        "source_hits": collector.hits,
        "observations": sorted(collector.observations.values(), key=lambda o: o["repo_id"]),
        "unresolved": collector.unresolved,
        "api_usage": api.usage_report(),
    }

    tcfg = config["tracking"]
    tracking_doc = {
        "schema_version": "1",
        "updated_at": store.iso(finished),
        "repos": store.update_tracking(tracking, seen, collector.gone_ids, business_date,
                                       tcfg["max_size"], tcfg["max_age_days"]),
    }

    previous = store.read_json(Path(data_dir) / "status.json", {}) or {}
    status_doc = {
        "schema_version": "1",
        "updated_at": store.iso(finished),
        "business_date": business_date,
        "last_run_id": run_id,
        "last_run_complete": complete,
        "last_complete_run_id": run_id if complete else previous.get("last_complete_run_id"),
        "last_complete_at": store.iso(finished) if complete else previous.get("last_complete_at"),
        "sources": [{"source_id": s["source_id"], "status": s["status"], "rows": s["rows"], "error": s["error"]}
                    for s in collector.sources],
        "counts": {"source_hits": len(collector.hits), "observations": len(run_doc["observations"]),
                   "unresolved": len(collector.unresolved), "tracking": len(tracking_doc["repos"])},
        "api_usage": run_doc["api_usage"],
    }

    outputs = [(store.run_path(business_date, run_id), run_doc, "run"),
               (Path("state") / "tracking.json", tracking_doc, "tracking"),
               (Path("status.json"), status_doc, "status")]
    for rel, doc, kind in outputs:
        store.validate(doc, kind)
    for rel, doc, _ in outputs:
        store.write_json(Path(out_dir) / rel, doc)
    return run_doc


def _code_version():
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _set_output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for key, value in values.items():
                f.write(f"{key}={value}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="radar")
    sub = parser.add_subparsers(dest="command", required=True)
    p_collect = sub.add_parser("collect", help="收集一次資料")
    p_collect.add_argument("--data-dir", required=True, help="資料分支的本機目錄（只讀）")
    p_collect.add_argument("--out-dir", required=True, help="新產生檔案的輸出目錄（必須是空的）")
    p_collect.add_argument("--config", default=str(ROOT / "config" / "radar.toml"))
    p_collect.add_argument("--force", action="store_true", help="今天已有完整紀錄也重新收集")
    p_collect.add_argument("--max-rest", type=int, help="覆寫每次執行的 REST 請求上限")
    p_validate = sub.add_parser("validate", help="驗證產物目錄")
    p_validate.add_argument("dir")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "validate":
        count = store.validate_tree(args.dir)
        print(f"驗證通過：{count} 個檔案")
        return 0

    out_dir = Path(args.out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        parser.error(f"輸出目錄不是空的：{out_dir}")

    config, config_sha = load_config(args.config)
    if args.max_rest is not None:
        config["max_rest_requests"] = args.max_rest
    started = store.now_taipei()
    event = os.environ.get("GITHUB_EVENT_NAME", "local")
    trigger = trigger_info(started, event, os.environ.get("RADAR_SCHEDULE"))
    run_suffix = "local"
    if os.environ.get("GITHUB_RUN_ID"):
        run_suffix = f"{os.environ['GITHUB_RUN_ID']}.{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    force = args.force or os.environ.get("RADAR_FORCE") == "true"

    session = requests.Session()
    api = GitHubApi(session, token=os.environ.get("GITHUB_TOKEN"), max_requests=config["max_rest_requests"],
                    max_wait=config["max_rate_wait_seconds"])
    run_doc = collect(config, config_sha, args.data_dir, out_dir, api, session, started, trigger,
                      _code_version(), run_suffix, force=force)
    if run_doc is None:
        print(f"{started.date()} 已有完整的收集紀錄，這次不重複執行")
        _set_output(changed="false")
        return 0

    ok = sum(s["status"] == "ok" for s in run_doc["sources"])
    print(f"run {run_doc['run_id']}：來源成功 {ok}/{len(run_doc['sources'])}，"
          f"命中 {len(run_doc['source_hits'])} 筆，觀測 {len(run_doc['observations'])} 個專案，"
          f"未解析 {len(run_doc['unresolved'])} 個，完整：{run_doc['complete']}")
    for s in run_doc["sources"]:
        if s["status"] != "ok":
            print(f"::warning::來源 {s['source_id']} 失敗：{s['error']}")
    print(f"API 用量：{run_doc['api_usage']}")
    _set_output(changed="true", run_id=run_doc["run_id"], complete=str(run_doc["complete"]).lower())
    return 0
