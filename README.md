# githubseeker：繁體中文 GitHub 熱門專案雷達

每天自動整理 GitHub 上正在變熱門的開源專案，最終目標是用繁體中文告訴非工程師：這個專案做什麼、適合誰、怎麼開始用。

> 本專案與 GitHub 官方無關。入選只代表近期熱門，不代表安全或推薦安裝。

## 目前進度

| 階段 | 內容 | 狀態 |
|---|---|---|
| A | 每天收集 Trending 與 Search 資料、觀測星數變化 | ✅ 進行中 |
| B | 每日精選、網站、RSS | 尚未開始 |
| C | 繁體中文摘要 | 尚未開始 |
| D | 依實際觀測計算成長、避免重複推薦 | 需先累積 7 天資料 |
| E | 搜尋、篩選、歷史回看、每週精選 | 尚未開始 |
| F | 連續觀測兩週後檢討 | 尚未開始 |

## 運作方式

GitHub Actions 每天在台北時間 02:17、06:17、10:17 各觸發一次（GitHub 排程常延遲數小時，所以設 3 次；當天已收集完整就直接結束）。

每次收集：

1. 抓 GitHub Trending 8 頁：全站、Python、TypeScript、中文專案 × 日榜、週榜。
2. 用 GitHub Search API 找最近 30 天、7 天新建立的熱門專案。
3. 用 GitHub API 補齊每個專案的最新資料，並持續觀測「追蹤名單」上專案的星數。
4. 結果提交到 [`radar-data`](../../tree/radar-data) 分支：

| 檔案 | 內容 |
|---|---|
| `runs/YYYY/MM/DD/<run_id>.json` | 每次收集的完整紀錄，寫入後不再修改 |
| `state/tracking.json` | 追蹤名單（最多 200 個專案） |
| `status.json` | 最近一次收集的狀態 |

資料原則：缺值存 `null`，真的是 0 才存 `0`；來源故障時不以測試資料填充；不保存 README 本文與貢獻者個人資料；抓取網頁時如實標示身分。

## 本機執行

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # macOS／Linux 用 .venv/bin/python
.venv/Scripts/python -m pytest
.venv/Scripts/python -m radar collect --data-dir <資料分支目錄> --out-dir <空的輸出目錄>
```

沒有設定 `GITHUB_TOKEN` 時，GitHub API 每小時只能呼叫 60 次，可以加 `--max-rest 45` 限制用量。

## 授權

程式碼採用 [MIT](LICENSE) 授權。設計時參考了 [HubDigest](https://github.com/redamancy36/HubDigest)、[rss_daily](https://github.com/RainLib/rss_daily)、[github-trending](https://github.com/jastfan/github-trending) 三個專案的概念，沒有複製它們的程式碼。
