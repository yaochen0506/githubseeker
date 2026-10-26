"""從資料分支重建整個網站：HTML、RSS、公開 JSON、sitemap。

網站是資料分支的純函數產物：同一份資料、同一版程式，產生的內容完全相同（建置時間除外）。
"""

import re
import shutil
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from . import store
from .rank import to_date
from .summarize import BARRIER_LABELS

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", ATOM_NS)


def _env():
    env = Environment(loader=FileSystemLoader(SITE_DIR / "templates"), autoescape=select_autoescape(["html"]),
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters["num"] = lambda n: f"{n:,}"
    return env


def _taipei_text(iso_text):
    dt = datetime.fromisoformat(iso_text).astimezone(store.TAIPEI)
    return f"{dt:%Y-%m-%d %H:%M}（台北時間）"


def edition_url_path(edition_id):
    y, m, d = edition_id.split("-")
    return f"archive/{y}/{m}/{d}/"


def _pick_view(pick):
    """把精選整理成範本好用的形式（換算都在這裡做，範本不寫邏輯）。用日期而不是「幾天前」，歷史頁才不會過期。"""
    created, pushed = to_date(pick["created_at"]), to_date(pick["pushed_at"])
    return {
        **pick,
        "created_date": created.isoformat() if created else None,
        "pushed_date": pushed.isoformat() if pushed else None,
        "og_image": f"https://opengraph.githubassets.com/1/{pick['full_name']}",
        "topics_shown": pick["topics"][:5],
        "summary_done": (pick.get("summary") or {}).get("status") == "done",
        "summary_failed": (pick.get("summary") or {}).get("status") == "failed",
        "barrier_label": BARRIER_LABELS.get(((pick.get("summary") or {}).get("content") or {}).get("barrier"), ""),
    }


def _edition_view(edition, boards):
    picks = [_pick_view(p) for p in edition["picks"]]
    return {
        **edition,
        "generated_text": _taipei_text(edition["generated_at"]),
        "main": [p for p in picks if p["slot"] == "main"],
        "discovery": [p for p in picks if p["slot"] == "discovery"],
        "failed_boards": [boards.get(s, s) for s in edition["coverage"]["sources_failed"]],
        "summaries_enabled": (edition.get("summary_stats") or {}).get("enabled", False),
        "summary_failed_count": sum(p["summary_failed"] for p in picks),
        "url_path": edition_url_path(edition["edition_id"]),
    }


def build_feed(editions, site_cfg):
    """RSS 2.0。識別碼與發布時間都來自已提交的精選，重新部署也不會改變。"""
    base = site_cfg["base_url"]
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    for tag, text in (("title", site_cfg["name"]), ("link", base), ("description", site_cfg["tagline"]),
                      ("language", "zh-TW")):
        ET.SubElement(channel, tag).text = text
    ET.SubElement(channel, f"{{{ATOM_NS}}}link", {"href": base + "feed.xml", "rel": "self", "type": "application/rss+xml"})
    if editions:
        latest = date.fromisoformat(editions[-1]["business_date"])
        cutoff = latest - timedelta(days=site_cfg["rss_days"] - 1)
        ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.fromisoformat(editions[-1]["generated_at"]))
        for edition in reversed(editions):
            if date.fromisoformat(edition["business_date"]) < cutoff:
                break
            pub = format_datetime(datetime.fromisoformat(edition["generated_at"]))
            for pick in edition["picks"]:
                item = ET.SubElement(channel, "item")
                label = "【新發現】" if pick["slot"] == "discovery" else ""
                ET.SubElement(item, "title").text = f"{label}{pick['full_name']}：{pick['reasons'][0]}"
                ET.SubElement(item, "link").text = f"{base}{edition_url_path(edition['edition_id'])}#repo-{pick['repo_id']}"
                ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"radar:{edition['edition_id']}:{pick['repo_id']}"
                ET.SubElement(item, "pubDate").text = pub
                summary = pick.get("summary") or {}
                lines = []
                if summary.get("status") == "done":
                    c = summary["content"]
                    lines += [c["one_liner"], "主要功能：" + "；".join(c["features"]),
                              f"使用門檻：{BARRIER_LABELS[c['barrier']]}，{c['barrier_note']}", "（AI 摘要，可能有誤）"]
                lines += pick["reasons"]
                if pick["description"]:
                    lines.append(f"原文描述：{pick['description']}")
                lines += pick["notes"] + [pick["html_url"]]
                ET.SubElement(item, "description").text = "\n".join(lines)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def build_sitemap(editions, site_cfg):
    base = site_cfg["base_url"]
    urlset = ET.Element("urlset", {"xmlns": "http://www.sitemaps.org/schemas/sitemap/0.9"})
    entries = [("", None), ("about/", None), ("archive/", None)]
    entries += [(edition_url_path(e["edition_id"]), e["business_date"]) for e in editions]
    for path, lastmod in entries:
        url = ET.SubElement(urlset, "url")
        ET.SubElement(url, "loc").text = base + path
        if lastmod:
            ET.SubElement(url, "lastmod").text = lastmod
    return ET.tostring(urlset, encoding="utf-8", xml_declaration=True)


def _md(text):
    """跳脫 Markdown 特殊字元，避免專案資料影響版面或變成連結。"""
    return re.sub(r"([\\`*_{}\[\]()<>#+!|~])", r"\\\1", text)


def release_notes(edition, site_cfg):
    """每日精選的版本發布內容（GitHub 會把它寄給關注儲存庫的人）。回傳 (標題, Markdown 內文)。"""
    page = site_cfg["base_url"] + edition_url_path(edition["edition_id"])
    lines = [f"**{edition['edition_id']} GitHub 熱門專案精選**｜[在網站上看完整精選]({page})", ""]
    if edition["status"] == "degraded":
        lines += ["這一天部分榜單抓取失敗，精選只依據其餘來源。", ""]
    for slot, heading in (("main", "主榜"), ("discovery", "新發現")):
        picks = [p for p in edition["picks"] if p["slot"] == slot]
        if not picks:
            continue
        lines += [f"### {heading}", ""]
        for p in picks:
            summary = p.get("summary") or {}
            text = summary["content"]["one_liner"] if summary.get("status") == "done" else p["reasons"][0]
            lines.append(f"{p['position']}. **[{p['full_name']}]({p['html_url']})**：{_md(text)}")
        lines.append("")
    if not edition["picks"]:
        lines += ["這一天沒有符合條件的專案。", ""]
    lines.append("入選只代表近期熱門，不代表安全或推薦安裝。")
    return f"{edition['edition_id']} 精選", "\n".join(lines) + "\n"


def site_marker(editions, code_version):
    """部署檢查用：網站上的這個檔案和預期不同，就需要重新部署。"""
    latest = editions[-1] if editions else None
    return {"edition_id": latest["edition_id"] if latest else None,
            "generated_at": latest["generated_at"] if latest else None,
            "code_version": code_version}


def build_site(data_dir, out_dir, site_cfg, score_cfg, code_version):
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"輸出目錄不是空的：{out}")
    editions = store.load_editions(data_dir)
    boards = site_cfg["boards"]
    views = [_edition_view(e, boards) for e in editions]
    latest = views[-1] if views else None
    env = _env()

    pages = [("index.html", "edition.html", {"edition": latest, "is_home": True}),
             ("archive/index.html", "archive.html", {"editions": list(reversed(views))}),
             ("about/index.html", "about.html", {})]
    pages += [(v["url_path"] + "index.html", "edition.html", {"edition": v, "is_home": False}) for v in views]

    for rel, template, ctx in pages:
        depth = rel.count("/")
        page = {
            "site": site_cfg, "score": score_cfg, "boards": boards, "latest": latest,
            "root": "../" * depth if depth else "./",
            "page_url": site_cfg["base_url"] + rel.removesuffix("index.html"),
            "stale_hours": site_cfg["stale_hours"], **ctx,
        }
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(env.get_template(template).render(**page), encoding="utf-8")

    shutil.copytree(SITE_DIR / "static", out / "static")
    (out / "feed.xml").write_bytes(build_feed(editions, site_cfg))
    (out / "sitemap.xml").write_bytes(build_sitemap(editions, site_cfg))
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site_cfg['base_url']}sitemap.xml\n",
                                    encoding="utf-8")
    for edition in editions:
        store.write_json(out / "data" / "editions" / f"{edition['edition_id']}.json", edition)
    if editions:
        store.write_json(out / "data" / "latest.json", editions[-1])
    status = store.read_json(Path(data_dir) / "status.json")
    if status:
        store.write_json(out / "status.json", status)
    store.write_json(out / "edition.json", site_marker(editions, code_version))
    return len(pages)


def live_marker(session, base_url):
    """抓線上的 edition.json；加上時間參數避開 CDN 快取。抓不到就回傳 None。"""
    try:
        resp = session.get(base_url + "edition.json", params={"t": int(time.time())}, timeout=20)
        return resp.json() if resp.status_code == 200 else None
    except (OSError, ValueError):  # 網路錯誤（requests 的例外屬於 OSError）或內容不是 JSON
        return None


def needs_deploy(live, expected):
    return live is None or any(live.get(k) != expected[k] for k in ("edition_id", "code_version"))
