# LEDERG CR:MP Launcher

Windows launcher and source code for the LEDERG CR:MP client.

## Current release

- Launcher: `release/LEDERG-CRMP-Launcher.exe`
- Public download page: https://oddly-hopeful-rudd.cloudpub.ru/
- Client files are downloaded individually into `GTA Criminal Russia`; a 4.6 GB ZIP is not required for the normal installation path.
- Individual files resume via HTTP Range and are verified by size/CRC32 before installation.
- The launcher checks a remote, generated SHA-256 manifest on startup and every 60 seconds. When files are added, changed, missing, or removed from the release, it offers an update; only LEDERG-managed files are pruned, and only after a successful sync.
- The ZIP endpoint remains available as a manual fallback.
- The visible branding and `movies/Logo.mpg` are LEDERG black/red. Original logo/loading assets were backed up on the server before replacement.

The game server runs separately on its game host. Cloudpub is used only to host the client download site and files.

## Radio repository

Radio tracks and station folder names live in [LEDERG-radio-crmp](https://github.com/LEDERG777-TEXNARIC/LEDERG-radio-crmp). The launcher syncs supported root-level `.mp3`, `.ogg`, and `.wav` tracks into `resound/radio/<station-name>` before starting the game; deleting a folder or song removes only previously synchronized tracks during the next successful sync.

**Runtime note:** the launcher downloads the Resound SA radio runtime directly from its upstream GitHub release on first launch, then synchronizes station tracks and creates LEDERG-branded station icons locally. The third-party plugin binaries are not mirrored in this repository or on the LEDERG download CDN.

## Development

Requirements: Windows, Python 3.12+ and internet access.

```powershell
python -m pip install -r requirements-build.txt
python -m unittest -v test_client_updates test_radio_sync test_radio_runtime test_folder_download test_launcher
.uild.ps1
```

To rebuild the x86 C# direct-launch helper as well (requires the .NET Framework compiler):

```powershell
.uild.ps1 -RebuildDirectHelper
```

The built EXE is copied to `release/LEDERG-CRMP-Launcher.exe`.

## Release configuration

The launcher reads `client.json` from this repository's `main` branch at runtime. The actual client manifest is generated from the server's client folder; its fingerprint changes when the file set or file contents change. The game server address remains in the separate `lederg-ip-crmp` repository.

The custom-radio runtime is downloaded directly from the upstream Resound SA GitHub release on first launch; its third-party binaries are not mirrored in LEDERG's client CDN. The launcher then syncs station tracks from the LEDERG radio index and generates LEDERG-branded station icons locally.

## Tests

The Windows test suite covers update detection and managed-file pruning, dynamic radio sync and station/song deletion, path traversal, Range resume, CRC failures, ZIP fallback, and server address parsing.
