# SPARKZ TRADER V2 - one-time setup (run SETUP_SPARKZ.bat; run it again any time to change something).
# Saves your settings in SPARKZ-TRADER-V2\.env on THIS computer only. That file is in .gitignore: it is never
# uploaded. The server and the MT5 bridge both read it, so you never type passwords into the windows again.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $root ".env"

$old = [ordered]@{}
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $v = $Matches[2].Trim()
            if ($v.Length -ge 2 -and (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'")))) {
                $v = $v.Substring(1, $v.Length - 2)
            } else { $v = ($v -split ' #')[0].Trim() }
            $old[$Matches[1]] = $v
        }
    }
}

function Ask([string]$label, [string]$key, [string]$default, [switch]$Secret) {
    $cur = if ($old.Contains($key) -and $old[$key]) { $old[$key] } else { $default }
    if ($Secret) {
        $hint = if ($cur) { "Enter = keep the saved one" } else { "Enter = skip" }
        $sec = Read-Host -AsSecureString "$label ($hint)"
        $v = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    } else {
        $v = Read-Host "$label [$cur]"
    }
    if ([string]::IsNullOrWhiteSpace($v)) { $v = $cur }
    return "$v".Trim()
}

Write-Host ""
Write-Host "SPARKZ TRADER V2 setup" -ForegroundColor Red
Write-Host "Press Enter to keep the value shown in [brackets]."
Write-Host ""

Write-Host "Where should the robot's orders go?"
Write-Host "  1 = your MT5 DEMO account (recommended)"
Write-Host "  2 = paper simulation only (MT5 prices, no orders in MT5)"
$curMode = if ($old["EXECUTION_MODE"] -eq "SIMULATION") { "2" } else { "1" }
$m = Ask "Choose 1 or 2" "_MODE" $curMode
$mode = if ($m -eq "2") { "SIMULATION" } else { "MT5_DEMO" }
if ($old["EXECUTION_MODE"] -eq "MT5_LIVE") {
    Write-Host "Your .env says MT5_LIVE (real account). Keep it? y/n" -ForegroundColor Yellow
    if ((Read-Host) -match '^[yY]') { $mode = "MT5_LIVE" }
}

$preset = Ask "Robot (assistant_daily = the daily gold robot)" "SPARKZ_PRESET" "assistant_daily"
$symbol = Ask "Gold's name in your MT5 Market Watch" "BRIDGE_SYMBOL" "XAUUSDm"
$watch = Ask "More MT5 markets for the Chart page (comma separated, no spaces)" "BRIDGE_WATCH" "EURUSDm,GBPUSDm,USDJPYm"
$tgToken = Ask "Telegram bot token from @BotFather" "TELEGRAM_BOT_TOKEN" "" -Secret
$tgChat = Ask "Telegram chat id (a number; Enter = skip)" "TELEGRAM_CHAT_ID" ""

# the server and the bridge read the same file, so the bridge password can simply be a long random one
$token = $old["SPARKZ_BRIDGE_TOKEN"]
if (-not $token) {
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = ([Convert]::ToBase64String($bytes)).Replace('+', 'A').Replace('/', 'B').Replace('=', '')
}

function Q([string]$v) { if ($v.Contains('"')) { return "'" + $v + "'" } return '"' + $v + '"' }
$managed = @("MARKET_DATA_PROVIDER", "EXECUTION_MODE", "SPARKZ_PRESET", "SPARKZ_BRIDGE_TOKEN", "TELEGRAM_BOT_TOKEN",
             "TELEGRAM_CHAT_ID", "BRIDGE_SYMBOL", "BRIDGE_WATCH", "BRIDGE_SERVER", "BRIDGE_TRADE", "_MODE")
$lines = @(
    "# SPARKZ TRADER V2 settings, written by setup_sparkz.ps1. Private: never share or upload this file.",
    "MARKET_DATA_PROVIDER=BRIDGE",
    "EXECUTION_MODE=$mode",
    "SPARKZ_PRESET=$preset",
    "SPARKZ_BRIDGE_TOKEN=$(Q $token)",
    "BRIDGE_SYMBOL=$symbol",
    "BRIDGE_WATCH=$watch",
    "BRIDGE_SERVER=http://127.0.0.1:8010",
    "BRIDGE_TRADE=$(if ($mode -eq 'SIMULATION') { 'false' } else { 'true' })"
)
if ($tgToken) { $lines += "TELEGRAM_BOT_TOKEN=$(Q $tgToken)" }
if ($tgChat) { $lines += "TELEGRAM_CHAT_ID=$(Q $tgChat)" }
foreach ($k in $old.Keys) {                       # anything else you added by hand stays
    if ($managed -notcontains $k) { $lines += "$k=$(Q $old[$k])" }
}
[IO.File]::WriteAllText($envFile, ($lines -join "`r`n") + "`r`n", (New-Object Text.UTF8Encoding $false))

Write-Host ""
Write-Host "Saved to $envFile" -ForegroundColor Green
Write-Host ("Orders: " + $mode + " | robot: " + $preset + " | gold: " + $symbol + " | chart markets: " + $watch)
Write-Host ("Telegram: " + $(if ($tgToken -and $tgChat) { "on" } else { "off" }))
Write-Host "Start SPARKZ with START_SPARKZ.bat"
