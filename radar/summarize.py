"""繁體中文摘要（C 階段）：取得並清理 README、查快取、呼叫 Claude Code、檢查、重試、守預算。

模型透過 `claude -p` 呼叫，用的是使用者的 Claude 訂閱權杖（CLAUDE_CODE_OAUTH_TOKEN），
workflow 只在這一步提供權杖。模型沒有任何工具可用，只能照 schema 輸出 JSON；
輸出還要通過長度、網址、簡體字與大陸用語檢查，不通過就附上問題重試一次。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import store
from .sources import ApiError, BudgetExhausted, NotFound, RateLimited

ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT / "config" / "summary_prompt.md"
URL_RE = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|io|org|net|dev|ai|app)\b", re.I)
BARRIER_LABELS = {"online": "免安裝（線上可用）", "install": "需要下載安裝", "code": "需要寫程式"}


# ---------- README ----------

def clean_readme(text, limit):
    """去掉程式碼、圖片徽章、HTML、連結網址，留下說明文字。"""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"(```|~~~)[\s\S]*?(\1|\Z)", "\n（程式碼省略）\n", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*\[[^\]]+\]:\s*\S+.*$", "", text, flags=re.M)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[​-‏⁠﻿]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:limit]


def fetch_readme(api, full_name):
    """回傳 README 原文；沒有 README 或取不到時回傳 None。"""
    try:
        return api.get_text(f"/repos/{full_name}/readme")
    except (NotFound, RateLimited, BudgetExhausted, ApiError):
        return None


def build_document(pick, readme):
    readme = readme.replace("</readme", "</ readme") if readme else "（沒有 README）"
    lines = [
        f"專案名稱：{pick['full_name']}",
        f"專案描述：{pick['description'] or '（無）'}",
        f"程式語言：{pick['language'] or '未標示'}",
        f"主題標籤：{', '.join(pick['topics']) or '（無）'}",
        f"授權：{pick['license_spdx'] or '未標示'}",
    ]
    return "\n".join(lines) + f"\n\n<readme>\n{readme}\n</readme>\n"


# ---------- 檢查 ----------

def check(content, cfg):
    """回傳問題清單；空清單代表通過。"""
    issues = [f"格式不符：{'/'.join(map(str, e.path)) or '整體'} {e.message}"
              for e in store.schema_errors(content, "summary")[:3]]
    texts = []
    for value in content.values() if isinstance(content, dict) else []:
        texts += value if isinstance(value, list) else [value]
    joined = "\n".join(t for t in texts if isinstance(t, str))
    if URL_RE.search(joined):
        issues.append("不可以包含網址或網域名稱")
    simplified = sorted({c for c in joined if c in cfg["simplified_chars"]})
    if simplified:
        issues.append(f"出現簡體字：{''.join(simplified[:10])}，請改用台灣繁體中文")
    terms = [f"「{t}」改為「{tw}」" for t, tw in cfg["mainland_terms"].items() if t in joined]
    if terms:
        issues.append("出現中國大陸用語：" + "、".join(terms[:5]))
    return issues


# ---------- 呼叫 Claude Code ----------

class ClaudeCodeRunner:
    """用 `claude -p` 產生一份摘要。回傳 (摘要或 None, 用量, 錯誤訊息或 None)。"""

    def __init__(self, cfg, binary="claude"):
        self.cfg = cfg
        self.binary = binary
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        # Claude Code 的檢查器不認得 draft 2020-12 的 $schema 宣告，傳給它時拿掉；欄位規則本身相容
        schema = store.read_json(store.SCHEMA_DIR / "summary.schema.json")
        self.schema = json.dumps({k: v for k, v in schema.items() if k not in ("$schema", "$id", "title")},
                                 ensure_ascii=False)

    def __call__(self, document, feedback=None):
        prompt = "請依照系統指示，摘要下面這個專案，並以指定的 JSON 格式回答。"
        if feedback:
            prompt += f"\n\n你上一次的回答有以下問題，請修正後重新回答：{feedback}"
        cmd = [self.binary, "-p", prompt,
               "--safe-mode", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
               "--model", self.cfg["model"], "--effort", self.cfg["effort"], "--max-turns", "4",
               "--system-prompt", self.system_prompt,
               "--output-format", "json", "--json-schema", self.schema]
        # 在空白目錄執行，避免讀到任何專案設定
        with tempfile.TemporaryDirectory() as cwd:
            try:
                proc = subprocess.run(cmd, input=document, capture_output=True, text=True, encoding="utf-8",
                                      timeout=self.cfg["call_timeout_seconds"], cwd=cwd, env=os.environ.copy())
            except subprocess.TimeoutExpired:
                return None, {}, "逾時"
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            return None, {}, f"無法解析 claude 的輸出（結束代碼 {proc.returncode}）：{proc.stderr.strip()[:200]}"
        usage = data.get("usage") or {}
        info = {
            "input_tokens": sum(usage.get(k) or 0 for k in
                                ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
            "output_tokens": usage.get("output_tokens") or 0,
            "cost_usd": data.get("total_cost_usd") or 0,
        }
        if data.get("is_error") or not isinstance(data.get("structured_output"), dict):
            return None, info, f"{data.get('subtype')}：{str(data.get('result'))[:200]}"
        return data["structured_output"], info, None


def availability(cfg, binary="claude"):
    """摘要能不能執行；不能時回傳原因（網站會照常運作，只是沒有中文摘要）。"""
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("RADAR_SUMMARY_LOCAL") == "1"):
        return False, "尚未設定 CLAUDE_CODE_OAUTH_TOKEN"
    if not shutil.which(binary):
        return False, "找不到 claude 指令"
    return True, None


# ---------- 主流程 ----------

def cache_key(input_sha, cfg):
    return hashlib.sha256(f"{cfg['prompt_version']}|{cfg['model']}|zh-TW|{input_sha}".encode()).hexdigest()[:20]


def cache_path(repo_id, key):
    return Path("summaries") / str(repo_id) / f"{key}.json"


def summarize_edition(edition, api, data_dir, out_dir, cfg, runner=None, clock=time.monotonic, now=store.now_taipei):
    """替精選裡的每個專案加上 summary 欄位，並在精選加上 summary_stats。直接修改傳入的 edition。"""
    stats = {"enabled": True, "reason": None, "model": cfg["model"], "attempted": 0, "done": 0, "failed": 0,
             "skipped": 0, "cached": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    if runner is None:
        ok, reason = availability(cfg)
        if not ok:
            stats.update(enabled=False, reason=reason, model=None, skipped=len(edition["picks"]))
            for pick in edition["picks"]:
                pick["summary"] = _record("skipped", None, None, error=reason)
            edition["summary_stats"] = stats
            return stats
        runner = ClaudeCodeRunner(cfg)

    deadline = clock() + cfg["total_timeout_seconds"]
    for pick in edition["picks"]:
        raw = fetch_readme(api, pick["full_name"])
        readme = clean_readme(raw, cfg["readme_max_chars"]) if raw else ""
        if not readme and not pick["description"]:
            pick["summary"] = _record("skipped", None, None, error="沒有 README 也沒有專案描述")
            stats["skipped"] += 1
            continue
        document = build_document(pick, readme)
        input_sha = hashlib.sha256(document.encode("utf-8")).hexdigest()
        key = cache_key(input_sha, cfg)

        cached = next((store.read_json(Path(d) / cache_path(pick["repo_id"], key))
                       for d in (out_dir, data_dir) if (Path(d) / cache_path(pick["repo_id"], key)).exists()), None)
        if cached:
            pick["summary"] = _record("done", cfg, key, has_readme=bool(readme), content=cached["content"],
                                      from_cache=True)
            stats["done"] += 1
            stats["cached"] += 1
            continue

        stats["attempted"] += 1
        content, feedback, error, attempts = None, None, None, 0
        for _ in range(cfg["max_attempts_per_repo"]):
            if stats["calls"] >= cfg["max_calls_per_run"] or clock() > deadline:
                error = "超過預算（呼叫次數或時間上限）"
                break
            stats["calls"] += 1
            attempts += 1
            result, info, error = runner(document, feedback)
            stats["input_tokens"] += info.get("input_tokens", 0)
            stats["output_tokens"] += info.get("output_tokens", 0)
            stats["cost_usd"] = round(stats["cost_usd"] + info.get("cost_usd", 0), 6)
            if error:
                feedback = None
                continue
            issues = check(result, cfg)
            if not issues:
                content = result
                break
            error = feedback = "；".join(issues)

        if content is None:
            pick["summary"] = _record("failed", cfg, key, has_readme=bool(readme), attempts=attempts,
                                      error=(error or "未知錯誤")[:300])
            stats["failed"] += 1
            continue
        cache_doc = {"schema_version": "1", "cache_key": key, "repo_id": pick["repo_id"],
                     "full_name": pick["full_name"], "created_at": store.iso(now()), "model": cfg["model"],
                     "prompt_version": cfg["prompt_version"], "input_sha256": input_sha,
                     "has_readme": bool(readme), "readme_chars": len(readme), "content": content}
        store.validate(cache_doc, "summary_cache")
        store.write_json(Path(out_dir) / cache_path(pick["repo_id"], key), cache_doc)
        pick["summary"] = _record("done", cfg, key, has_readme=bool(readme), content=content, attempts=attempts)
        stats["done"] += 1

    edition["summary_stats"] = stats
    return stats


def _record(status, cfg, key, has_readme=None, content=None, from_cache=False, attempts=0, error=None):
    return {"status": status, "content": content,
            "model": cfg["model"] if cfg else None, "prompt_version": cfg["prompt_version"] if cfg else None,
            "cache_key": key, "has_readme": has_readme, "from_cache": from_cache, "attempts": attempts,
            "error": error}


def needs_alert(stats, cfg):
    """實際呼叫模型的專案中，成功比例太低就要通知（稽核報告 C-07：不能靜默失敗）。"""
    if not stats["enabled"] or stats["attempted"] == 0:
        return False
    return (stats["done"] - stats["cached"]) / stats["attempted"] < cfg["alert_below_success_rate"]
