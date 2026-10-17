param(
    [switch]$RebuildDirectHelper
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    throw "Python 3.12+ was not found in PATH. Install Python and rerun build.ps1."
}
$Python = $PythonCommand.Source

if ($RebuildDirectHelper) {
    $Compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
    if (-not (Test-Path $Compiler)) {
        throw "The x86 .NET Framework compiler csc.exe was not found."
    }
    & $Compiler /nologo /target:winexe /platform:x86 "/out:$Root\LEDERG-DirectLaunch.exe" "$Root\direct_launcher.cs"
    if ($LASTEXITCODE -ne 0) { throw "Direct-launch helper compilation failed." }
}

& $Python -m unittest -v test_folder_download test_launcher
if ($LASTEXITCODE -ne 0) { throw "Unit tests failed; refusing to build." }

& $Python -m PyInstaller --noconfirm --clean "$Root\LEDERG CRMP.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

$Release = Join-Path $Root "release"
New-Item -ItemType Directory -Path $Release -Force | Out-Null
Copy-Item "$Root\dist\LEDERG CRMP.exe" "$Release\LEDERG-CRMP-Launcher.exe" -Force
Get-Item "$Release\LEDERG-CRMP-Launcher.exe" | Select-Object FullName,Length,LastWriteTime
