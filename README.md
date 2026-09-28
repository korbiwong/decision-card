# 決策卡（港股 + 美股）— 自己 host 嘅版本

唔使 connector，唔會限速。GitHub 幫你收市後掃全市場、交易時段每 15 分鐘更新報價同推提醒；網頁由 GitHub Pages host，加到主畫面有自己嘅 icon 同名。

## 一次過設定（約 15 分鐘）

1. **GitHub 開 repo**：github.com → New repository → 名 `decision-card` → **Public** → Create。
2. **Upload 檔案**：Add file → Upload files → 將呢個 folder 入面所有嘢拖入去（包括 `.github` folder——如果拖唔到，就喺 repo 用 Add file → Create new file，路徑打 `.github/workflows/scan.yml`，貼內容；`quotes.yml` 同樣）→ Commit。
3. **開 Pages**：Settings → Pages → Source 揀「Deploy from a branch」→ Branch `main`、folder `/ (root)` → Save。等 1–2 分鐘，頁頂會出網址 `https://<你嘅名>.github.io/decision-card/`。
4. **通知**（選填）：裝 ntfy app → subscribe 一個你作嘅 topic 名 → Settings → Secrets and variables → Actions → New repository secret → `NTFY_TOPIC` = 個名。
5. **跑第一次掃描**：Actions tab → 「scan」→ Run workflow。約 8–12 分鐘（港股 2,000 幾隻 + 美股 500 幾隻）。
6. 開 Pages 網址，就見到全市場嘅卡。手機 Safari → 分享 → 加入主畫面 → icon 係投資風格嗰個，名係「決策卡」。

## 日常

- 收市後（港股 17:40、美股 17:30 ET）自動重掃，推一個總覽通知。
- 交易時段每 15 分鐘更新 `quotes.json`：網頁開住每 3 分鐘讀一次。
- 提醒／真實買賣／paper trade 記喺手機；想 GitHub 幫你背景推提醒，喺網頁「設定」撳匯出，貼落 `alerts.json` / `real.json` / `trades.json`。
- 「檢討」採納咗參數，撳「匯出參數」貼落 `params.json`，之後每次掃描都用新參數。
- 開會／總結＋論據／新聞分析要 Anthropic API key（console.anthropic.com，用量計費，一次總結約 US$0.02–0.05）。

## 限制
- Yahoo 報價延遲約 15 分鐘，加上 GitHub 每 15 分鐘先跑一次，最差可以遲 30 分鐘。做即日炒賣唔夠，做幾日至幾星期嘅決策夠。
- GitHub Actions 免費額度：public repo 無限，呢個設定每月約 60 小時，遠低於限制。

## 「即時」到底有幾即時（老實版）

| 市場 | 免費做到 | 真即時要點做 |
|---|---|---|
| 美股 | **Finnhub 免費 key → 真即時，每 10 秒**（網頁「設定」貼 key） | 已經係 |
| 港股 | Yahoo 延遲 15 分鐘 + GitHub 每 5 分鐘一次 → 最差遲 20 分鐘 | 港交所即時數據係收費嘅：接 moomoo/富途 OpenAPI（開戶就有即時價，但要喺一部長開嘅電腦跑 OpenD），或者付費數據商 |

即日炒港股請用 moomoo 本身嘅即時報價同提醒；呢個 app 負責揀股、計止蝕、記錄同檢討。
