# githubseeker：繁體中文 GitHub 熱門專案雷達

每天自動整理 GitHub 上正在變熱門的開源專案，最終目標是用繁體中文告訴非工程師：這個專案做什麼、適合誰、怎麼開始用。

網站：https://yaochen0506.github.io/githubseeker/ （RSS：[feed.xml](https://yaochen0506.github.io/githubseeker/feed.xml)）

> 本專案與 GitHub 官方無關。入選只代表近期熱門，不代表安全或推薦安裝。

## 目前進度

| 階段 | 內容 | 狀態 |
|---|---|---|
| A | 每天收集 Trending 與 Search 資料、觀測星數變化 | ✅ 運作中 |
| B | 每日精選、網站、RSS | ✅ 運作中 |
| C | 繁體中文摘要（Claude，使用訂閱權杖） | ✅ 已完成，設定權杖後啟用 |
| D | 依實際觀測計算成長、避免重複推薦 | 需先累積 7 天資料 |
| E | 搜尋、篩選、歷史回看、每週精選 | 尚未開始 |
| F | 連續觀測兩週後檢討 | 尚未開始 |

## 運作方式

GitHub Actions 每天在台北時間 02:17、06:17、10:17 各觸發一次（GitHub 排程常延遲數小時，所以設 3 次；當天已收集完整、精選也已產生，就只檢查網站是否需要重新部署）。

每次收集：

1. 抓 GitHub Trending 8 頁：全站、Python、TypeScript、中文專案 × 日榜、週榜。
2. 用 GitHub Search API 找最近 30 天、7 天新建立的熱門專案。
3. 用 GitHub API 補齊每個專案的最新資料，並持續觀測「追蹤名單」上專案的星數。
4. 收集完整後挑出每日精選，並請 Claude 依專案的 README 寫繁體中文摘要（一句話、主要功能、適合誰、用途、使用門檻、限制）：主榜最多 7 個、新發現最多 3 個，7 天內入選過的不重複（選法見 `config/score.toml` 與網站的說明頁）。
5. 結果提交到 [`radar-data`](../../tree/radar-data) 分支，再從資料分支重建網站、部署到 GitHub Pages：

| 檔案 | 內容 |
|---|---|
| `runs/YYYY/MM/DD/<run_id>.json` | 每次收集的完整紀錄，寫入後不再修改 |
| `editions/YYYY/MM/DD.json` | 每日精選（含中文摘要），寫入後不再修改 |
| `summaries/<專案編號>/<鍵>.json` | 摘要快取：README 沒變就不重寫 |
| `state/tracking.json` | 追蹤名單（最多 200 個專案） |
| `status.json` | 最近一次收集的狀態 |

資料原則：缺值存 `null`，真的是 0 才存 `0`；來源故障時不以測試資料填充；不保存 README 本文與貢獻者個人資料；抓取網頁時如實標示身分。

## 本機執行

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # macOS／Linux 用 .venv/bin/python
.venv/Scripts/python -m pytest
.venv/Scripts/python -m radar collect --data-dir <資料分支目錄> --out-dir <空的輸出目錄>
.venv/Scripts/python -m radar edition --data-dir <資料分支目錄> --out-dir <同一個輸出目錄>
.venv/Scripts/python -m radar build-site --data-dir <資料分支目錄> --out-dir <空的網站目錄>
```

中文摘要需要 `claude` 指令與 Claude 訂閱：在 GitHub Actions 用 Secret `CLAUDE_CODE_OAUTH_TOKEN`（以 `claude setup-token` 產生）；本機可設 `RADAR_SUMMARY_LOCAL=1` 改用本機登入。沒有權杖時精選照常產生，只是沒有中文摘要。

沒有設定 `GITHUB_TOKEN` 時，GitHub API 每小時只能呼叫 60 次，可以加 `--max-rest 45` 限制用量。

## 授權

程式碼採用 [MIT](LICENSE) 授權；網站上整理的精選清單與入選理由採 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.zh-hant) 授權。設計時參考了 [HubDigest](https://github.com/redamancy36/HubDigest)、[rss_daily](https://github.com/RainLib/rss_daily)、[github-trending](https://github.com/jastfan/github-trending) 三個專案的概念，沒有複製它們的程式碼。
