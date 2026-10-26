# githubseeker 專案說明（給 Claude）

繁體中文 GitHub 熱門專案雷達。依 A～F 階段開發，目前完成 A 階段（每日收集）與 B 階段（每日精選、網站、RSS）。網站：https://yaochen0506.github.io/githubseeker/

## 設計依據

- 本機 `docs/`（已加入 .gitignore，不在公開儲存庫）：`plan-v0.1-codex.md` 計畫書、`radar-audit-claude-v1.md` 稽核報告、`plan-review-local-v1.md` 開工前檢討、`phase-b-plan.md` B 階段計畫。新階段開工前先讀這些文件，尤其是稽核報告 §4、§5、§7。

## 不可違反的原則

- 日期一律用台北時間（`store.TAIPEI`，固定 +08:00）。
- 缺值存 `null`，真的是 0 才存 `0`；Trending 回 200 但列數太少視為來源失敗。
- `runs/` 只新增、不修改；資料分支 `radar-data` 是唯一事實來源。
- 來源故障時不注入 mock／測試資料。
- 抓網頁時用設定檔中如實的 User-Agent，不偽裝瀏覽器。
- 處理外部資料的 job 只給 `contents: read`；寫入資料分支的 job 先跑 `python -m radar validate`。
- 第三方 action 用 commit SHA 固定版本；`workflow_dispatch` 的輸入只透過 `env` 傳入，不直接內插進 `run:`。

## 常用指令

```bash
.venv/Scripts/python -m pytest
.venv/Scripts/python -m radar collect --data-dir <dir> --out-dir <empty dir> --max-rest 45
.venv/Scripts/python -m radar validate <out dir>
.venv/Scripts/python -m radar build-site --data-dir <dir> --out-dir <empty dir>
```

## 結構

- `radar/sources.py`：Trending 解析、GitHub API（限流、用量）
- `radar/store.py`：資料分支讀寫、追蹤名單、schema 驗證
- `radar/pipeline.py`：收集流程、產生精選、命令列入口
- `radar/rank.py`：資格、每日成長、分數、名額、冷卻
- `radar/editorial.py`：入選理由與提醒（不用 AI）
- `radar/site.py`：從資料分支重建網站、RSS、JSON；部署檢查
- `site/templates/`、`site/static/`：網頁範本（Jinja2 自動跳脫）與 CSS／JS
- `schema/`：run、edition、tracking、status 的 JSON Schema
- `config/radar.toml`：榜單、搜尋條件、上限；`score.toml`：選法常數（改了要更新 score_version）；`site.toml`：網站設定
- `.github/workflows/collect.yml`：collect（唯讀）→ persist（寫資料分支）→ site-check → deploy（Pages）＋ heartbeat（每月心跳）
