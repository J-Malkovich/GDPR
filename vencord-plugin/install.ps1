# Builds Vencord with the SecretCrypt plugin and patches Discord (Windows PowerShell).
# Usage: .\install.ps1 [-PublicKey age1...] [-Dest C:\path]   (default ~\Vencord-secretcrypt)
param([string]$PublicKey = $env:GDPR_PUBKEY, [string]$Dest = "$HOME\Vencord-secretcrypt", [switch]$SkipInject)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$ageVersion = "0.3.1"
if (-not $PublicKey -and (Test-Path "$here\..\gdpr_age_key.pub")) { $PublicKey = Get-Content "$here\..\gdpr_age_key.pub" -Raw }
if (-not $PublicKey) { $PublicKey = Read-Host "GDPR public key (age1..., printed by the init step)" }
$PublicKey = $PublicKey.Trim()
if ($PublicKey -notmatch '^age1[0-9a-z]{50,}$') { throw "not an age public key: $PublicKey" }
foreach ($t in "git","node","npm") { if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { throw "missing: $t" } }
function Pnpm { if (Get-Command pnpm -ErrorAction SilentlyContinue) { & pnpm @args } else { & npx --yes pnpm @args }; if ($LASTEXITCODE) { throw "pnpm $args failed" } }

if (-not (Test-Path "$Dest\.git")) { git clone --depth 1 https://github.com/Vendicated/Vencord.git $Dest; if ($LASTEXITCODE) { throw "clone failed" } }
Set-Location $Dest
git checkout -q -- package.json pnpm-lock.yaml 2>$null
Pnpm install --frozen-lockfile
Pnpm add "age-encryption@$ageVersion" -w
New-Item -ItemType Directory -Force src\userplugins | Out-Null
Remove-Item -Recurse -Force src\userplugins\secretCrypt -ErrorAction SilentlyContinue
Copy-Item -Recurse "$here\secretCrypt" src\userplugins\secretCrypt
$ts = "src\userplugins\secretCrypt\index.ts"
(Get-Content $ts -Raw).Replace('default: "" // GDPR_PUBKEY', "default: `"$PublicKey`" // GDPR_PUBKEY") | Set-Content $ts -NoNewline
if (-not (Select-String -Path $ts -SimpleMatch $PublicKey -Quiet)) { throw "could not set the key" }
Pnpm build
if ($SkipInject) { "built; skipping inject"; return }
Pnpm inject
"`nDone. Restart Discord, enable SecretCrypt in Settings > Vencord > Plugins."
"Every /secret must show the green 'Secret encrypted, sending' toast."
"Update later: git pull in $Dest and re-run this script. Re-run after Discord updates too."
