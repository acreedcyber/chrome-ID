"""
Chrome-Stats daily refresh.

Pulls today's Chrome-Stats snapshots, strips them down to the columns the KQL
detection rule needs, and writes each result to data/ with a stable filename
so externaldata URLs never have to be updated again.

Supported Chrome-Stats endpoints:

    GET https://chrome-stats.com/api/chrome/download-raw-data
        ?type=obsolete
        &key=obsolete-extensions/{YYYY}/obsolete-extensions-{YYYYMMDD}.csv

    GET https://chrome-stats.com/api/chrome/download-raw-data
        ?type=mini
        &key=mini-ranking-stats/{YYYY}/mini-ranking-stats-{YYYYMMDD}.csv

Daily snapshots are published with a date stamp. If today's file is not
available yet, the script falls back through the previous LOOKBACK_DAYS days
until it finds one.

Environment variables:
    CHROME_STATS_API_KEY    your Chrome-Stats premium API key (required)

Output:
    data/obsolete-extensions.csv   columns: id,name,obsoleteReason
    data/mini-ranking-stats.csv    columns: id,name
"""

from __future__ import annotations

import csv
import io
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

API_BASE = "https://chrome-stats.com/api/chrome/download-raw-data"
LOOKBACK_DAYS = 7
REQUEST_TIMEOUT = 120

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Dataset:
    name: str
    data_type: str
    key_prefix: str
    file_prefix: str
    output_file: Path
    keep_columns: list[str]
    cleanup_glob: str


DATASETS = [
    Dataset(
        name="obsolete extensions",
        data_type="obsolete",
        key_prefix="obsolete-extensions",
        file_prefix="obsolete-extensions",
        output_file=DATA_DIR / "obsolete-extensions.csv",
        keep_columns=["id", "name", "obsoleteReason"],
        cleanup_glob="*obsolete*.csv",
    ),
    Dataset(
        name="mini ranking stats",
        data_type="mini",
        key_prefix="mini-ranking-stats",
        file_prefix="mini-ranking-stats",
        output_file=DATA_DIR / "mini-ranking-stats.csv",
        keep_columns=["id", "name"],
        cleanup_glob="*mini*.csv",
    ),
]

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("fetch_chromestats")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

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


def _build_url(dataset: Dataset, d: date) -> str:
    """Build the download URL for a dataset/date pair."""
    key = f"{dataset.key_prefix}/{d.year}/{dataset.file_prefix}-{d.strftime('%Y%m%d')}.csv"
    return f"{API_BASE}?type={dataset.data_type}&key={key}"


def _session(api_key: str) -> requests.Session:
    """Create a Chrome-Stats session with the configured API key."""
    s = requests.Session()
    s.headers.update({
        "X-API-Key": api_key,
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/csv, */*",
        "User-Agent": "chrome-ID-refresh/1.0 (+github.com/acreedcyber/chrome-ID)",
    })
    return s


def _download_for_date(
    session: requests.Session,
    dataset: Dataset,
    d: date,
) -> str | None:
    """Return CSV text on success, None on 404, and raise on other errors."""
    url = _build_url(dataset, d)
    log.info("[%s] trying %s", dataset.name, url)
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 404:
        log.info("[%s]   -> 404 (not published)", dataset.name)
        return None
    if resp.status_code in {401, 403}:
        log.error(
            "[%s]   -> HTTP %s; auth rejected. body=%s",
            dataset.name,
            resp.status_code,
            resp.text[:300],
        )
        resp.raise_for_status()
    resp.raise_for_status()

    ct = resp.headers.get("Content-Type", "")
    text = resp.text
    if "html" in ct.lower() or text.lstrip().startswith("<"):
        raise RuntimeError(
            f"[{dataset.name}] expected CSV but got Content-Type={ct!r}; "
            f"first 200 chars: {text[:200]!r}"
        )
    log.info("[%s]   -> 200 (%d bytes)", dataset.name, len(text))
    return text


def _trim_to_required_columns(
    csv_text: str,
    dataset: Dataset,
) -> tuple[list[str], list[list[str]]]:
    """Return rows restricted to dataset.keep_columns, preserving order."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise RuntimeError(f"[{dataset.name}] downloaded CSV has no header row")

    missing = [c for c in dataset.keep_columns if c not in reader.fieldnames]
    if missing:
        raise RuntimeError(
            f"[{dataset.name}] downloaded CSV is missing required columns: "
            f"{missing}. Available: {reader.fieldnames}"
        )

    rows: list[list[str]] = []
    for record in reader:
        rows.append([(record.get(c) or "").strip() for c in dataset.keep_columns])
    return dataset.keep_columns[:], rows


def _atomic_write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Write CSV atomically so partial writes cannot corrupt the live file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        writer.writerows(rows)
    tmp.replace(path)


def _remove_previous_dataset_csvs(dataset: Dataset) -> int:
    """Remove old matching CSVs from data/, keeping the stable output file."""
    if not DATA_DIR.exists():
        return 0

    removed = 0
    for path in DATA_DIR.glob(dataset.cleanup_glob):
        if path.resolve() == dataset.output_file.resolve():
            continue
        if path.is_file():
            path.unlink()
            removed += 1
    return removed


def _refresh_dataset(session: requests.Session, dataset: Dataset) -> bool:
    csv_text: str | None = None
    used_date: date | None = None

    for offset in range(LOOKBACK_DAYS + 1):
        d = date.today() - timedelta(days=offset)
        try:
            csv_text = _download_for_date(session, dataset, d)
        except requests.HTTPError as e:
            log.error("[%s] HTTP error downloading %s: %s", dataset.name, d, e)
            return False
        if csv_text is not None:
            used_date = d
            break

    if csv_text is None or used_date is None:
        log.error(
            "[%s] no CSV found in the last %d days",
            dataset.name,
            LOOKBACK_DAYS + 1,
        )
        return False

    log.info("[%s] using snapshot dated %s", dataset.name, used_date.isoformat())

    header, rows = _trim_to_required_columns(csv_text, dataset)
    removed = _remove_previous_dataset_csvs(dataset)
    if removed:
        log.info("[%s] removed %d previous CSV file(s)", dataset.name, removed)

    _atomic_write_csv(dataset.output_file, header, rows)
    log.info(
        "[%s] wrote %s (%d rows, columns=%s)",
        dataset.name,
        dataset.output_file,
        len(rows),
        header,
    )
    return True


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> int:
    _load_local_env()
    api_key = os.environ.get("CHROME_STATS_API_KEY")
    if not api_key:
        log.error("CHROME_STATS_API_KEY environment variable is not set")
        return 2

    session = _session(api_key)
    for dataset in DATASETS:
        if not _refresh_dataset(session, dataset):
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
