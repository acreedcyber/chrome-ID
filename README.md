# chrome-ID — automated obsolete-extensions refresh

Automates the daily refresh of the **obsolete-extensions** feed.

The other two feeds in the KQL (`mini-ranking-stats` and the Edge list) are
still managed manually. This automation does not touch them. (yet......)

## How it works

Every day at 09:00 UTC, GitHub Actions runs a Python script that:

1. Downloads today's obsolete-extensions snapshot from the Chrome-Stats API:
   ```
   GET https://chrome-stats.com/api/chrome/download-raw-data
       ?type=obsolete
       &key=obsolete-extensions/{YYYY}/obsolete-extensions-{YYYYMMDD}.csv
   ```
   Falls back through the previous 7 days if today's file isn't published yet.

2. Trims the CSV down to `id, name, obsoleteReason`.

3. Writes `data/obsolete-extensions.csv` — a **stable** filename that never
   changes, so the KQL `externaldata` URL is wired once and stays valid.

4. Commits and pushes back to `main` only if the file actually changed.

## One-command setup

After you rotate your Chrome-Stats API key within the Github Actions, open PowerShell in this folder
and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

The bootstrap script handles everything in one shot:

- verifies `git` and `gh` are installed and you're signed in,
- prompts for your API key (input is hidden, never logged),
- clones the existing `acreedcyber/chrome-ID` repo,
- merges these scaffold files into the clone,
- removes the historical dated `obsolete-extensions-*.csv` files,
- commits and pushes everything to `main`,
- registers the API key as the `CHROME_STATS_API_KEY` repo secret,
- triggers the first workflow run so you can verify it in the Actions tab.


### Prerequisites the bootstrap will check for

| Tool | Install once |
|---|---|
| Git for Windows | <https://git-scm.com/download/win> |
| GitHub CLI | `winget install --id GitHub.cli` |
| `gh` auth | `gh auth login` (HTTPS, login via browser) |

## Running the fetch script locally (for testing)

```powershell
pip install -r scripts/requirements.txt
$env:CHROME_STATS_API_KEY = "your-key"
python scripts/fetch_chromestats.py
```

It writes to `data/obsolete-extensions.csv` relative to the repo root.

## Repo layout

```
.
├── .github/workflows/
│   └── daily-refresh.yml          # daily cron + commit/push
├── data/
│   └── obsolete-extensions.csv    # generated, stable filename
├── kql/
│   └── browser-extensions-detection.kql
├── scripts/
│   ├── bootstrap.ps1              # one-command setup (run once)
│   ├── fetch_chromestats.py       # what the daily workflow runs
│   ├── migrate_remove_legacy_csvs.sh  # manual fallback (bootstrap also does this)
│   └── requirements.txt
└── README.md
```

