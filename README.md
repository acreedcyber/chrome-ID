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

