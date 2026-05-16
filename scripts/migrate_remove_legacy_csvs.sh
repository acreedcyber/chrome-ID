#!/usr/bin/env bash
# One-time migration: remove the historical obsolete-extensions CSVs from the
# repo root. They are being replaced by data/obsolete-extensions.csv, which
# the daily workflow now refreshes automatically.
#
# Files kept on purpose (still managed manually for now):
#   - mini-ranking-stats-*.csv    (feeds ExtensionNameMap in KQL)
#   - edge results (*).csv         (feeds EdgeExtension in KQL)
#
# Usage (from repo root):
#     bash scripts/migrate_remove_legacy_csvs.sh
#
# Re-runnable: skips files that are already gone.

set -euo pipefail

# Explicit list of legacy obsolete-extensions files known to be in the repo.
# If you have more accumulated, add them here.
LEGACY_FILES=(
  "obsolete-extensions-2025091012.csv"
  "Plains 3-26 obsolete-extensions-20260326.csv"
)

# Also auto-detect any other dated obsolete-extensions files at the repo root
# so accumulated history doesn't linger. The data/ folder is excluded — that's
# where the new stable file lives.
mapfile -t AUTO_DETECTED < <(
  git ls-files \
    | grep -Ei '^obsolete-extensions[-_].*\.csv$|^.*obsolete-extensions-[0-9]+\.csv$' \
    | grep -v '^data/' || true
)

# Merge + dedupe
declare -A SEEN
TO_REMOVE=()
for f in "${LEGACY_FILES[@]}" "${AUTO_DETECTED[@]}"; do
  if [[ -n "$f" && -z "${SEEN[$f]:-}" ]]; then
    SEEN[$f]=1
    TO_REMOVE+=("$f")
  fi
done

removed=0
for f in "${TO_REMOVE[@]}"; do
  if git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
    echo "removing: $f"
    git rm -- "$f"
    removed=$((removed + 1))
  else
    echo "skip:     $f (not tracked)"
  fi
done

if [[ "$removed" -eq 0 ]]; then
  echo "Nothing to remove."
  exit 0
fi

git commit -m "chore: remove legacy obsolete-extensions CSVs, replaced by data/obsolete-extensions.csv"
echo ""
echo "Removed $removed legacy file(s). Push when ready:  git push"
