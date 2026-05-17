"""
Chrome-Stats daily refresh — obsolete-extensions only.

Pulls today's "obsolete extensions" snapshot from Chrome-Stats, strips it down
to the three columns the KQL detection rule needs (id, name, obsoleteReason),
and writes the result to data/obsolete-extensions.csv with a STABLE filename
so the KQL query's externaldata URL never has to be updated again.

The Chrome-Stats download endpoint shape was confirmed against the user's
account:

    GET https://chrome-stats.com/api/chrome/download-raw-data
        ?type=obsolete
        &key=obsolete-extensions/{YYYY}/obsolete-extensions-{YYYYMMDD}.csv

Daily snapshots are published with a date stamp. If today's file isn't
available yet (timezone lag, weekend, publishing delay), the script falls
back through the previous LOOKBACK_DAYS days until it finds one.

Output:
    data/obsolete-extensions.csv   columns: id,name,obsoleteReason
"""

from __future__ import annotations

import csv
import io
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

# Configuration

API_BASE = "https://chrome-stats.com/api/chrome/download-raw-data"
LOOKBACK_DAYS = 7        # if today's file isn't published yet, search back N days
REQUEST_TIMEOUT = 120    # seconds; the file can be sizeable

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "obsolete-extensions.csv"
OBSOLETE_CSV_GLOB = "*obsolete*.csv"

# Columns we keep. Anything else in the source CSV is dropped.
KEEP_COLUMNS = ["id", "name", "obsoleteReason"]

# Logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fetch_chromestats")


# Helpers

def _load_local_env() -> None:
    """Load simple KEY=value pairs from a repo-local .env file, if present."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def _build_url(d: date) -> str:
    """Build the download URL for a given date."""
    key = f"obsolete-extensions/{d.year}/obsolete-extensions-{d.strftime('%Y%m%d')}.csv"
    return f"{API_BASE}?type=obsolete&key={key}"


def _session(api_key: str) -> requests.Session:
    """Send the key under both common header names — whichever Chrome-Stats
    expects will be honored, the other ignored. Saves a runtime auth probe."""
    s = requests.Session()
    s.headers.update({
        "X-API-Key": api_key,
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/csv, */*",
        "User-Agent": "chrome-ID-refresh/1.0 (+github.com/acreedcyber/chrome-ID)",
    })
    return s


def _download_for_date(session: requests.Session, d: date) -> str | None:
    """Try to download the CSV for date `d`. Return CSV text on success,
    None on 404 (file not yet published), raise on other errors."""
    url = _build_url(d)
    log.info("trying %s", url)
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 404:
        log.info("  -> 404 (not published)")
        return None
    if resp.status_code == 401 or resp.status_code == 403:
        # Surface auth errors immediately — no point falling back
        log.error("  -> HTTP %s; auth rejected. body=%s",
                  resp.status_code, resp.text[:300])
        resp.raise_for_status()
    resp.raise_for_status()

    # Guard against the "I returned HTML" case (login page, error page, etc.)
    ct = resp.headers.get("Content-Type", "")
    text = resp.text
    if "html" in ct.lower() or text.lstrip().startswith("<"):
        raise RuntimeError(
            f"expected CSV but got Content-Type={ct!r}; "
            f"first 200 chars: {text[:200]!r}"
        )
    log.info("  -> 200 (%d bytes)", len(text))
    return text


def _trim_to_required_columns(csv_text: str) -> tuple[list[str], list[list[str]]]:
    """Return (header, rows) restricted to KEEP_COLUMNS, preserving order."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise RuntimeError("downloaded CSV has no header row")

    missing = [c for c in KEEP_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise RuntimeError(
            f"downloaded CSV is missing required columns: {missing}. "
            f"Available: {reader.fieldnames}"
        )

    rows: list[list[str]] = []
    for record in reader:
        rows.append([(record.get(c) or "").strip() for c in KEEP_COLUMNS])
    return KEEP_COLUMNS[:], rows


def _atomic_write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Write CSV atomically (temp file + rename) so partial writes can't
    corrupt the file that the KQL query pulls from."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        writer.writerows(rows)
    tmp.replace(path)


def _remove_previous_obsolete_csvs() -> int:
    """Remove old obsolete CSVs from data/, keeping OUTPUT_FILE."""
    if not DATA_DIR.exists():
        return 0

    removed = 0
    for path in DATA_DIR.glob(OBSOLETE_CSV_GLOB):
        if path.resolve() == OUTPUT_FILE.resolve():
            continue
        if path.is_file():
            path.unlink()
            removed += 1
    return removed


# Entry point

def main() -> int:
    _load_local_env()
    api_key = os.environ.get("CHROME_STATS_API_KEY")
    if not api_key:
        log.error("CHROME_STATS_API_KEY environment variable is not set")
        return 2

    session = _session(api_key)

    csv_text: str | None = None
    used_date: date | None = None
    for offset in range(LOOKBACK_DAYS + 1):
        d = date.today() - timedelta(days=offset)
        try:
            csv_text = _download_for_date(session, d)
        except requests.HTTPError as e:
            log.error("HTTP error downloading %s: %s", d, e)
            return 1
        if csv_text is not None:
            used_date = d
            break

    if csv_text is None or used_date is None:
        log.error("no obsolete CSV found in the last %d days", LOOKBACK_DAYS + 1)
        return 1

    log.info("using snapshot dated %s", used_date.isoformat())

    header, rows = _trim_to_required_columns(csv_text)
    removed = _remove_previous_obsolete_csvs()
    if removed:
        log.info("removed %d previous obsolete CSV file(s)", removed)
    _atomic_write_csv(OUTPUT_FILE, header, rows)
    log.info("wrote %s (%d rows, columns=%s)", OUTPUT_FILE, len(rows), header)
    return 0


if __name__ == "__main__":
    sys.exit(main())
