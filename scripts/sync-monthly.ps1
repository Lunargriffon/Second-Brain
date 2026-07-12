$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Invoke-NativeSilently {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]] $Arguments
    )

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments *> $null
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Add-SqliteToPath {
    if (Get-Command sqlite3 -ErrorAction SilentlyContinue) {
        return
    }

    $sqlite = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter sqlite3.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($sqlite) {
        $env:PATH = "$($sqlite.DirectoryName);$env:PATH"
    }
}

function Get-XCookieValues {
    $path = Join-Path $repoRoot ".env"
    if (-not (Test-Path $path)) {
        throw ".env is required for X bookmark sync"
    }

    $cookies = @{}
    foreach ($line in Get-Content $path) {
        $trimmed = $line.Trim()
        if (-not $trimmed) {
            continue
        }

        if ($trimmed -match "^(X_CT0|CT0)=(.+)$") {
            $cookies["ct0"] = $Matches[2].Trim()
            continue
        }
        if ($trimmed -match "^(X_AUTH_TOKEN|AUTH_TOKEN)=(.+)$") {
            $cookies["auth_token"] = $Matches[2].Trim()
            continue
        }
    }

    if (-not $cookies.ContainsKey("ct0") -or -not $cookies.ContainsKey("auth_token")) {
        throw ".env must contain X_CT0 and X_AUTH_TOKEN"
    }
    return $cookies
}

function Sync-XBookmarks {
    Add-SqliteToPath
    $cookies = Get-XCookieValues

    $exitCode = Invoke-NativeSilently npx --yes fieldtheory sync `
        --cookies $cookies["ct0"] $cookies["auth_token"] `
        --no-media `
        --yes `
        --max-minutes 30

    $source = Join-Path $env:USERPROFILE ".fieldtheory\bookmarks\bookmarks.jsonl"
    if ($exitCode -ne 0 -and -not (Test-Path $source)) {
        throw "X bookmark sync failed with exit code $exitCode"
    }
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $repoRoot "data\raw\x-bookmarks.fieldtheory.jsonl") -Force
    }
}

function Sync-ZhihuDefaultCollection {
    $env:PYTHONPATH = "src"
    $exitCode = Invoke-NativeSilently python -m pkb.cli export zhihu-batch `
        --collections-file data/config/zhihu-collections.txt `
        --output-dir data/raw `
        --state-dir data/state `
        --limit 0 `
        --request-delay 2 `
        --max-retry 3
    if ($exitCode -ne 0) {
        throw "Zhihu default collection sync failed with exit code $exitCode"
    }

    $raw = Join-Path $repoRoot "data\raw\zhihu-1003243192.jsonl"
    if (Test-Path $raw) {
        $exitCode = Invoke-NativeSilently python -m pkb.cli images zhihu `
            --raw data/raw/zhihu-1003243192.jsonl `
            --output-dir data/images/zhihu/1003243192 `
            --state data/state/images-1003243192.state.json `
            --limit 0 `
            --request-delay 2
        if ($exitCode -ne 0) {
            throw "Zhihu default collection image sync failed with exit code $exitCode"
        }
    }
}

Sync-XBookmarks
Sync-ZhihuDefaultCollection
