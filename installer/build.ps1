<#
.SYNOPSIS
  Builds PaperTablet-Setup-<version>.exe and PaperTablet-Portable-<version>.zip in
  installer\dist\. Both carry their own Python runtime, so the user's PC needs no Python, pip or
  admin rights.

.DESCRIPTION
  1. Downloads the embeddable CPython into installer\build\runtime\python, enables site-packages
     and pip-installs pc\requirements.txt into it.
  2. Copies pythonw.exe as "Paper Tablet.exe" with the app's icon and name (rcedit), so Task
     Manager and the taskbar don't say "Python".
  3. Downloads the WebView2 Evergreen bootstrapper (setup runs it only if WebView2 is missing).
  4. Stages the app in installer\build\app with the repo's folder layout (pc\, tablet\), copying
     the tablet files byte-exact.
  5. Zips a portable copy: a small launcher exe (launcher.cs, compiled with the C# compiler
     that ships with Windows), the app, the runtime and a portable.txt marker.
  6. Compiles paper-tablet.iss with Inno Setup 6 (ISCC).

  Based on Kallisti's Case Manager\Build recipe. Downloads in steps 1-3 are skipped when already
  done; -Clean starts over.

.EXAMPLE
  .\installer\build.ps1
  .\installer\build.ps1 -Clean
  .\installer\build.ps1 -SkipSetup    # everything except ISCC (the portable zip is still built)
#>

param(
    [string]$PyVersion = "3.13.15",
    [switch]$Clean,
    [switch]$SkipSetup
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is many times slower with the bar
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Here
$Build = Join-Path $Here "build"
$PyDir = Join-Path $Build "runtime\python"
$Stage = Join-Path $Build "app"
$Prereqs = Join-Path $Build "prereqs"
$Tools = Join-Path $Build "tools"
$Dist = Join-Path $Here "dist"
$Icon = Join-Path $Here "paper-tablet.ico"

$m = Select-String -Path (Join-Path $Repo "pc\version.py") -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $m) { throw "No __version__ in pc\version.py" }
$Version = $m.Matches[0].Groups[1].Value
Write-Host "Paper Tablet $Version"

if ($Clean -and (Test-Path $Build)) {
    Write-Host "Removing $Build..."
    Remove-Item -Recurse -Force $Build
}
New-Item -ItemType Directory -Force -Path $PyDir, $Prereqs, $Tools, $Dist | Out-Null

function Download($url, $out) {
    if (-not (Test-Path $out)) {
        Write-Host "Downloading $url"
        Invoke-WebRequest -Uri $url -OutFile "$out.part"
        Move-Item -Force "$out.part" $out
    }
}

# -- 1. Embedded Python + packages -------------------------------------------
$py = Join-Path $PyDir "python.exe"
if (-not (Test-Path $py)) {
    $zip = Join-Path $Build "python-$PyVersion-embed-amd64.zip"
    Download "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" $zip
    Expand-Archive -Path $zip -DestinationPath $PyDir -Force
}

# The embeddable zip ships with site disabled; turn it on and add site-packages. app.py puts its
# own folder on sys.path, so no sitecustomize is needed (unlike Kallisti).
$pth = Get-ChildItem -Path $PyDir -Filter "python3*._pth" | Select-Object -First 1
if (-not $pth) { throw "No python3xx._pth in $PyDir" }
$lines = Get-Content $pth.FullName
$lines = $lines -replace '^#\s*import site', 'import site'
if ($lines -notcontains "Lib\site-packages") { $lines += "Lib\site-packages" }
Set-Content -Path $pth.FullName -Value $lines

if (-not (Test-Path (Join-Path $PyDir "Scripts\pip.exe"))) {
    $getPip = Join-Path $Build "get-pip.py"
    Download "https://bootstrap.pypa.io/get-pip.py" $getPip
    & $py $getPip --no-warn-script-location
    if ($LASTEXITCODE) { throw "get-pip failed" }
}
Write-Host "Installing pc\requirements.txt..."
& $py -m pip install --no-warn-script-location --disable-pip-version-check -r (Join-Path $Repo "pc\requirements.txt")
if ($LASTEXITCODE) { throw "pip install failed" }
& $py -c "import webview, paramiko; print('pywebview', webview.__version__ if hasattr(webview, '__version__') else '?', '/ paramiko', paramiko.__version__)"
if ($LASTEXITCODE) { throw "the bundled runtime can't import webview/paramiko" }

# -- 2. Branded launcher exe --------------------------------------------------
$rcedit = Join-Path $Tools "rcedit-x64.exe"
Download "https://github.com/electron/rcedit/releases/latest/download/rcedit-x64.exe" $rcedit
$appExe = Join-Path $PyDir "Paper Tablet.exe"
Copy-Item (Join-Path $PyDir "pythonw.exe") $appExe -Force
$fileVersion = "$Version.0"
& $rcedit $appExe --set-icon $Icon `
    --set-version-string "FileDescription" "Paper Tablet" `
    --set-version-string "ProductName" "Paper Tablet" `
    --set-version-string "OriginalFilename" "Paper Tablet.exe" `
    --set-version-string "InternalName" "Paper Tablet" `
    --set-version-string "CompanyName" "Paper Tablet" `
    --set-file-version $fileVersion --set-product-version $fileVersion
if ($LASTEXITCODE) { throw "rcedit failed" }

# -- 3. WebView2 bootstrapper -------------------------------------------------
Download "https://go.microsoft.com/fwlink/p/?LinkId=2124703" (Join-Path $Prereqs "MicrosoftEdgeWebView2Setup.exe")

# -- 4. Stage the app ---------------------------------------------------------
# Same layout as the repo: app.py finds web\ and ..\tablet\ relative to itself.
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
$pcOut = Join-Path $Stage "pc"
New-Item -ItemType Directory -Force -Path $pcOut, (Join-Path $Stage "tablet\companion") | Out-Null
Get-ChildItem (Join-Path $Repo "pc") -Filter *.py |
    Where-Object { $_.Name -notlike "test_*" } |
    ForEach-Object { Copy-Item $_.FullName $pcOut }
Copy-Item (Join-Path $Repo "pc\requirements.txt") $pcOut
Copy-Item (Join-Path $Repo "pc\web") $pcOut -Recurse
# Linux binaries and a shell script for the tablet: Copy-Item keeps the bytes (and run.sh's LF).
foreach ($f in "tablet\rmpp-pen", "tablet\companion\rmpp-companion", "tablet\companion\run.sh") {
    Copy-Item (Join-Path $Repo $f) (Join-Path $Stage $f)
}
Copy-Item (Join-Path $Repo "tablet\*.pdf") (Join-Path $Stage "tablet")
foreach ($f in "tablet\rmpp-pen", "tablet\companion\rmpp-companion", "tablet\companion\run.sh") {
    $a = (Get-FileHash (Join-Path $Repo $f)).Hash
    $b = (Get-FileHash (Join-Path $Stage $f)).Hash
    if ($a -ne $b) { throw "$f changed while copying" }
}
# Marker files (see pc\paths.py). "installed": log to %LOCALAPPDATA%\PaperTablet instead of next
# to the code. "portable.txt": keep settings, key and log in data\ beside the app.
$Markers = Join-Path $Build "markers"
New-Item -ItemType Directory -Force -Path $Markers | Out-Null
Set-Content -Path (Join-Path $Markers "installed") -Value "Installed by PaperTablet-Setup-$Version.exe"
Set-Content -Path (Join-Path $Markers "portable.txt") -Value @"
This file makes Paper Tablet portable: settings, calibration, the app's key and the log stay in
the data folder next to it, and nothing is left on the PC. Delete it to use the normal places
(%APPDATA%\PaperTablet and %USERPROFILE%\.ssh) instead.
"@

# -- 5. Portable zip ----------------------------------------------------------
# PaperTablet-Portable-<version>.zip holds one "Paper Tablet" folder: the launcher exe, pc\,
# tablet\, runtime\python\ and portable.txt.
$Portable = Join-Path $Build "portable\Paper Tablet"
if (Test-Path (Split-Path $Portable)) { Remove-Item -Recurse -Force (Split-Path $Portable) }
New-Item -ItemType Directory -Force -Path (Join-Path $Portable "runtime") | Out-Null
Copy-Item (Join-Path $Stage "*") $Portable -Recurse
Copy-Item $PyDir (Join-Path $Portable "runtime") -Recurse
Copy-Item (Join-Path $Markers "portable.txt") $Portable

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "C# compiler not found: $csc" }
$src = Join-Path $Build "launcher.cs"
(Get-Content (Join-Path $Here "launcher.cs") -Raw).Replace('"0.0.0.0"', "`"$fileVersion`"") | Set-Content $src
& $csc /nologo /target:winexe /optimize+ /win32icon:$Icon "/out:$(Join-Path $Portable 'Paper Tablet.exe')" /r:System.Windows.Forms.dll $src
if ($LASTEXITCODE) { throw "launcher compile failed" }

$zip = Join-Path $Dist "PaperTablet-Portable-$Version.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($Portable, $zip, [IO.Compression.CompressionLevel]::Optimal, $true)
$z = Get-Item $zip
Write-Host ("Built {0} ({1:N1} MB)" -f $z.FullName, ($z.Length / 1MB))

if ($SkipSetup) {
    Write-Host "Setup not built (-SkipSetup)."
    exit 0
}

# -- 6. Inno Setup ------------------------------------------------------------
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
              "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
              "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found. Install it from https://jrsoftware.org/isdl.php" }
& $iscc "/DAppVersion=$Version" (Join-Path $Here "paper-tablet.iss")
if ($LASTEXITCODE) { throw "ISCC failed" }

$out = Get-Item (Join-Path $Dist "PaperTablet-Setup-$Version.exe")
Write-Host ("Built {0} ({1:N1} MB)" -f $out.FullName, ($out.Length / 1MB))
