# One-command bootstrap for chrome-ID daily automation.
#
# What it does (in order):
#   1. Verifies git + GitHub CLI are installed and gh is authenticated
#   2. Prompts for your Chrome-Stats API key (never echoed, never logged)
#   3. Clones the existing acreedcyber/chrome-ID repo into a temp folder
#   4. Copies the scaffold files into the clone
#   5. Removes the legacy obsolete-extensions CSVs
#   6. Commits everything and pushes to main
#   7. Sets CHROME_STATS_API_KEY as a repo secret via gh CLI
#   8. Triggers the first workflow run so you can verify it works
#
# After this completes, the only remaining manual step is updating your
# Sentinel rule with the KQL from kql/browser-extensions-detection.kql.
#
# Usage (from the scaffold folder):
#     powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
#
# Prerequisites:
#     - Git for Windows:  https://git-scm.com/download/win
#     - GitHub CLI:       winget install --id GitHub.cli
#     - One-time auth:    gh auth login   (choose HTTPS, login with browser)

[CmdletBinding()]
param(
    [string]$Repo    = 'acreedcyber/chrome-ID',
    [string]$WorkDir = (Join-Path $env:TEMP 'chrome-ID-bootstrap'),
    [string]$Branch  = 'main'
)

$ErrorActionPreference = 'Stop'

function Info($msg) { Write-Host "[bootstrap] $msg" -ForegroundColor Cyan }
function Done($msg) { Write-Host "[bootstrap] $msg" -ForegroundColor Green }
function Die($msg)  { Write-Host "[bootstrap] ERROR: $msg" -ForegroundColor Red; exit 1 }

# --------------------------------------------------------------------------- #
# 1. Prerequisites
# --------------------------------------------------------------------------- #

Info 'checking prerequisites'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die 'git not found. Install: https://git-scm.com/download/win'
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Die "gh (GitHub CLI) not found. Install:  winget install --id GitHub.cli"
}

gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Die "GitHub CLI is not authenticated. Run:  gh auth login   (then re-run this script)"
}
Done 'git, gh, and gh auth all good'

# Identify the scaffold folder (the parent of /scripts/, which is where this script lives)
$ScaffoldDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Info "scaffold dir: $ScaffoldDir"

# --------------------------------------------------------------------------- #
# 2. Prompt for API key
# --------------------------------------------------------------------------- #

Write-Host ''
Info 'Paste your Chrome-Stats API key (input will be hidden):'
$ApiKeySecure = Read-Host -AsSecureString -Prompt '  api key'

$ApiKeyPlain = [System.Net.NetworkCredential]::new('', $ApiKeySecure).Password
if ([string]::IsNullOrWhiteSpace($ApiKeyPlain)) { Die 'API key cannot be empty' }

# --------------------------------------------------------------------------- #
# 3. Clone existing repo into a fresh working dir
# --------------------------------------------------------------------------- #

if (Test-Path $WorkDir) {
    Info "removing stale working clone at $WorkDir"
    Remove-Item -Recurse -Force $WorkDir
}
Info "cloning https://github.com/$Repo.git -> $WorkDir"
git clone --depth 50 "https://github.com/$Repo.git" $WorkDir
if ($LASTEXITCODE -ne 0) { Die 'git clone failed' }

# --------------------------------------------------------------------------- #
# 4. Copy scaffold files into the clone (preserve clone's .git)
# --------------------------------------------------------------------------- #

Info 'copying scaffold files into clone'
$Items = Get-ChildItem -Path $ScaffoldDir -Force | Where-Object { $_.Name -notin '.git' }
foreach ($Item in $Items) {
    $Target = Join-Path $WorkDir $Item.Name
    if ($Item.PSIsContainer) {
        Copy-Item -Path $Item.FullName -Destination $Target -Recurse -Force
    } else {
        Copy-Item -Path $Item.FullName -Destination $Target -Force
    }
}

# --------------------------------------------------------------------------- #
# 5. Remove legacy obsolete-extensions files from the clone
# --------------------------------------------------------------------------- #

Push-Location $WorkDir
try {
    Info 'looking for legacy obsolete-extensions files to remove'

    # Anything at any path that looks like an obsolete-extensions dated CSV,
    # EXCLUDING our new stable file at data/obsolete-extensions.csv.
    $TrackedFiles = git ls-files
    $LegacyMatches = @()
    foreach ($f in $TrackedFiles) {
        if ($f -eq 'data/obsolete-extensions.csv') { continue }
        if ($f -match '(?i)obsolete-extensions') { $LegacyMatches += $f }
    }

    if ($LegacyMatches.Count -eq 0) {
        Info '  none found'
    } else {
        foreach ($f in $LegacyMatches) {
            Info "  rm: $f"
            git rm -- "$f" *> $null
        }
    }

    # --------------------------------------------------------------------- #
    # 6. Commit + push
    # --------------------------------------------------------------------- #
    Info 'staging + committing'
    git add -A
    git -c user.name='chrome-stats-bot' -c user.email='chrome-stats-bot@users.noreply.github.com' `
        commit -m 'chore: automate daily obsolete-extensions refresh + remove legacy files'

    if ($LASTEXITCODE -ne 0) {
        # No changes to commit — surface this and move on
        Info 'nothing to commit (scaffold already in sync with main)'
    } else {
        Info "pushing to origin/$Branch"
        git push origin "HEAD:$Branch"
        if ($LASTEXITCODE -ne 0) { Die 'git push failed' }
        Done 'push complete'
    }
}
finally {
    Pop-Location
}

# --------------------------------------------------------------------------- #
# 7. Set the GitHub repo secret
# --------------------------------------------------------------------------- #

Info 'setting CHROME_STATS_API_KEY secret on the repo'
$ApiKeyPlain | gh secret set CHROME_STATS_API_KEY --repo $Repo --body -
$ExitGh = $LASTEXITCODE

# Wipe the plaintext key from memory ASAP
$ApiKeyPlain = $null
Remove-Variable ApiKeyPlain -ErrorAction SilentlyContinue
[System.GC]::Collect()

if ($ExitGh -ne 0) { Die 'gh secret set failed' }
Done 'secret set'

# --------------------------------------------------------------------------- #
# 8. Trigger the first workflow run
# --------------------------------------------------------------------------- #

Info 'triggering first workflow run'
gh workflow run daily-refresh.yml --repo $Repo --ref $Branch
if ($LASTEXITCODE -ne 0) { Die 'failed to trigger workflow' }

# Give GitHub a moment to register the run, then show its status
Start-Sleep -Seconds 4
Info 'most recent run:'
gh run list --repo $Repo --workflow daily-refresh.yml --limit 1

Write-Host ''
Done '==========================================================='
Done 'Bootstrap complete.'
Write-Host ''
Write-Host 'Watch the run in real time:'
Write-Host "    gh run watch --repo $Repo"
Write-Host ''
Write-Host 'Or open the Actions tab:'
Write-Host "    https://github.com/$Repo/actions"
Write-Host ''
Write-Host 'Final step: paste the contents of kql/browser-extensions-detection.kql'
Write-Host 'into your Sentinel analytics rule.'
Done '==========================================================='
