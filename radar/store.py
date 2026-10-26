"""資料分支的讀寫：run 檔（只新增）、追蹤名單、狀態檔，以及 JSON Schema 驗證。

資料分支 radar-data 的版面：
  runs/YYYY/MM/DD/<run_id>.json   每次執行一檔，寫入後不再修改
  editions/YYYY/MM/DD.json         每日精選，寫入後不再修改
  state/tracking.json              追蹤名單，每次執行重寫
  status.json                      最近一次執行的狀態摘要
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

# 台灣沒有日光節約時間，用固定 +08:00 即可，不必依賴系統時區資料庫
TAIPEI = timezone(timedelta(hours=8), "Asia/Taipei")
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"

# 寫入資料分支時只接受這幾種路徑（防止產物夾帶其他檔案）
ALLOWED_PATHS = {
    re.compile(r"^runs/\d{4}/\d{2}/\d{2}/\d{8}T\d{6}-[A-Za-z0-9.-]+\.json$"): "run",
    re.compile(r"^editions/\d{4}/\d{2}/\d{2}\.json$"): "edition",
    re.compile(r"^state/tracking\.json$"): "tracking",
    re.compile(r"^status\.json$"): "status",
}


def now_taipei():
    return datetime.now(TAIPEI).replace(microsecond=0)


def iso(dt):
    return dt.isoformat()


def run_path(business_date, run_id):
    y, m, d = business_date.split("-")
    return Path("runs") / y / m / d / f"{run_id}.json"


def edition_path(business_date):
    y, m, d = business_date.split("-")
    return Path("editions") / y / m / f"{d}.json"


def load_runs(data_dir, business_date):
    day_dir = Path(data_dir) / run_path(business_date, "x").parent
    return [read_json(p) for p in sorted(day_dir.glob("*.json"))] if day_dir.is_dir() else []


def load_editions(data_dir):
    """所有每日精選，依日期由舊到新。"""
    paths = sorted((Path(data_dir) / "editions").glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9].json"))
    return [read_json(p) for p in paths]


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, doc):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def validate(doc, kind):
    """不符合 schema 就丟出例外，避免壞資料進到資料分支。"""
    schema = read_json(SCHEMA_DIR / f"{kind}.schema.json")
    errors = sorted(Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        raise ValueError(f"{kind} schema 驗證失敗（共 {len(errors)} 處），第一處在 {where}：{first.message}")


def validate_tree(root):
    """驗證整個產物目錄：每個檔案的路徑都要在白名單內，內容都要符合對應的 schema。"""
    root = Path(root)
    files = [p for p in root.rglob("*") if p.is_file()]
    if not files:
        raise ValueError("產物目錄是空的")
    for path in files:
        rel = path.relative_to(root).as_posix()
        kind = next((k for pattern, k in ALLOWED_PATHS.items() if pattern.match(rel)), None)
        if kind is None:
            raise ValueError(f"不允許的檔案路徑：{rel}")
        validate(read_json(path), kind)
    return len(files)


def complete_run_exists(data_dir, business_date):
    """今天（台北日期）是否已經有一份完整成功的 run 檔。"""
    return any(run.get("complete") is True for run in load_runs(data_dir, business_date))


def load_tracking(data_dir):
    doc = read_json(Path(data_dir) / "state" / "tracking.json")
    return doc["repos"] if doc else []


def update_tracking(entries, seen, gone_ids, business_date, max_size, max_age_days):
    """更新追蹤名單。

    seen：本次在 Trending／Search 出現、且已確認編號的 (repo_id, full_name)。
    gone_ids：API 回 404 的專案，直接移除。
    """
    today = date.fromisoformat(business_date)
    by_id = {e["repo_id"]: dict(e) for e in entries if e["repo_id"] not in gone_ids}
    for repo_id, full_name in seen:
        entry = by_id.setdefault(repo_id, {"repo_id": repo_id, "full_name": full_name,
                                           "first_seen": business_date, "last_seen": business_date})
        entry["full_name"] = full_name
        entry["last_seen"] = business_date
    cutoff = (today - timedelta(days=max_age_days)).isoformat()
    kept = [e for e in by_id.values() if e["last_seen"] >= cutoff]
    kept.sort(key=lambda e: (e["last_seen"], e["first_seen"], e["repo_id"]), reverse=True)
    return kept[:max_size]
