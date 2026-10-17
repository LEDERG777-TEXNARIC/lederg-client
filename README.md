# LEDERG CR:MP Launcher

Windows launcher and source code for the LEDERG CR:MP client.

## Current release

- Launcher: `release/LEDERG-CRMP-Launcher.exe`
- Public download page: https://oddly-hopeful-rudd.cloudpub.ru/
- The launcher downloads the client **as individual files into `GTA Criminal Russia`**, rather than downloading a 4.6 GB ZIP first.
- Each file is downloaded to a temporary `.lederg.part` file, supports HTTP Range resume, and is verified against the file manifest before being installed.
- The ZIP endpoint remains available as a manual fallback.

The game server runs separately on its game host. Cloudpub is used only to host the client download site and files.

## Development

Requirements: Windows, Python 3.12+ and internet access.

```powershell
python -m pip install -r requirements-build.txt
python -m unittest -v test_folder_download test_launcher
.uild.ps1
```

To rebuild the x86 C# direct-launch helper as well (requires the .NET Framework compiler):

```powershell
.uild.ps1 -RebuildDirectHelper
```

The built EXE is copied to `release/LEDERG-CRMP-Launcher.exe`.

## Release configuration

The launcher reads `client.json` from this repository's `main` branch at runtime. The game server address is kept in the separate `lederg-ip-crmp` repository so its address can be changed without rebuilding the launcher.

## Tests

Unit tests cover client manifest/path validation, direct per-file downloads, Range resume, CRC failures, ZIP fallback/resume, and server address parsing.
