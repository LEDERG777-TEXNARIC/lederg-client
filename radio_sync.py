from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

import requests

RESOUND_RUNTIME_VERSION = "1.2-alpha"
RESOUND_RELEASE_URL = "https://github.com/true-goniss/resound-SA/releases/download/1.2-alpha/resound_SA.v1.2-alpha.zip"
RESOUND_REQUIRED_FILES = (
    "resound_SA.asi",
    "bass.dll",
    "resound/fonts/Bank_Gothic_Medium.ttf",
    "resound/sounds/radiosystem/radioselect.mp3",
    "resound/textures/radio_icons/1.png",
    "resound/icons_util/img_funx.exe",
)
RESOUND_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
RESOUND_MAX_EXTRACTED_BYTES = 300 * 1024 * 1024
RADIO_REPOSITORY = "LEDERG777-TEXNARIC/LEDERG-radio-crmp"
RADIO_BRANCH = "main"
TREE_URL = f"https://api.github.com/repos/{RADIO_REPOSITORY}/git/trees/{RADIO_BRANCH}?recursive=1"
RAW_BASE_URL = f"https://raw.githubusercontent.com/{RADIO_REPOSITORY}/{RADIO_BRANCH}/"
RADIO_INDEX_URL = RAW_BASE_URL + "radio-index.json"
RADIO_RELATIVE_ROOT = Path("resound") / "radio"
RADIO_STATE_FILENAME = ".lederg-radio-state.json"
SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".ogg", ".wav"}
STATE_SCHEMA = 1
USER_AGENT = "LEDERG-RadioSync/1.0"

def ensure_resound_runtime(game_root: Path, http=None, archive_url: str = RESOUND_RELEASE_URL) -> dict:
    """
    Install the radio-wheel runtime from its upstream GitHub release.

    The third-party plugin is downloaded directly from its author's release
    rather than mirrored into LEDERG's distribution. Station music remains in
    LEDERG-radio-crmp and is synchronized separately.
    """
    parsed = urlparse(str(archive_url))
    expected_path_prefix = "/true-goniss/resound-SA/releases/download/"
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "github.com"
        or not parsed.path.startswith(expected_path_prefix)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Radio runtime must be downloaded from the official Resound SA GitHub release.")

    root = Path(game_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Game client directory does not exist: {root}")

    required = [root.joinpath(*PurePosixPath(item).parts) for item in RESOUND_REQUIRED_FILES]
    marker = root / "resound" / ".lederg-radio-state-runtime.json"
    marker_payload = {}
    try:
        loaded_marker = json.loads(marker.read_text(encoding="utf-8"))
        if isinstance(loaded_marker, dict):
            marker_payload = loaded_marker
    except (OSError, ValueError, TypeError):
        pass
    if all(path.is_file() and path.stat().st_size > 0 for path in required):
        if marker_payload.get("version") == RESOUND_RUNTIME_VERSION:
            return {"ok": True, "installed": False, "files": 0, "source": archive_url}
        if not marker_payload:
            # Adopt an already installed compatible runtime without re-downloading it.
            _atomic_write(marker, {"schema": 1, "version": RESOUND_RUNTIME_VERSION, "source": archive_url})
            return {"ok": True, "installed": False, "files": 0, "source": archive_url}

    client_http = http or requests.Session()
    if hasattr(client_http, "headers"):
        client_http.headers.update({"User-Agent": USER_AGENT})
    try:
        response = client_http.get(archive_url, timeout=(15, 60))
        response.raise_for_status()
        payload = response.content
    except Exception as exc:
        raise RuntimeError(f"Could not download the official Resound SA radio runtime: {exc}") from exc

    if not payload or len(payload) > RESOUND_MAX_ARCHIVE_BYTES:
        raise RuntimeError("Resound SA release archive is empty or larger than the allowed 100 MB.")
    if not zipfile.is_zipfile(io.BytesIO(payload)):
        raise RuntimeError("Official Resound SA release did not return a valid ZIP archive.")

    with tempfile.TemporaryDirectory(prefix="lederg-resound-runtime-") as temporary:
        stage = Path(temporary)
        installed_paths = {}
        total_size = 0
        try:
            with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename.replace("\\", "/")
                    relative = PurePosixPath(name)
                    if (
                        not name
                        or relative.is_absolute()
                        or name.startswith("/")
                        or "\x00" in name
                        or any(part in ("", ".", "..") for part in name.split("/"))
                        or (relative.parts and ":" in relative.parts[0])
                    ):
                        raise ValueError(f"Unsafe path in Resound release archive: {info.filename!r}")
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK:
                        raise ValueError(f"Symlinks are not allowed in Resound release archives: {name}")
                    normalized = relative.as_posix()
                    if normalized not in {"resound_SA.asi", "bass.dll"} and not normalized.startswith("resound/"):
                        continue
                    if info.file_size < 0 or info.file_size > RESOUND_MAX_EXTRACTED_BYTES:
                        raise ValueError(f"Invalid file size in Resound release archive: {normalized}")
                    total_size += info.file_size
                    if total_size > RESOUND_MAX_EXTRACTED_BYTES:
                        raise ValueError("Resound release expands beyond the 300 MB safety limit.")
                    target = stage.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination, length=1024 * 1024)
                    if target.stat().st_size != info.file_size:
                        raise RuntimeError(f"Truncated file in Resound release archive: {normalized}")
                    installed_paths[normalized.casefold()] = (normalized, target)
        except (zipfile.BadZipFile, OSError) as exc:
            raise RuntimeError(f"Could not read Resound SA release archive: {exc}") from exc

        missing = [
            item for item in RESOUND_REQUIRED_FILES
            if item.casefold() not in installed_paths
            or not installed_paths[item.casefold()][1].is_file()
            or installed_paths[item.casefold()][1].stat().st_size == 0
        ]
        if missing:
            raise RuntimeError("Resound release is missing required files: " + ", ".join(missing))

        # Validate the complete archive before touching the user's game folder.
        for relative_name, staged_file in installed_paths.values():
            relative = PurePosixPath(relative_name)
            target = root.joinpath(*relative.parts)
            try:
                target.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Resound file would escape the game folder: {relative_name}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_target = target.with_name(target.name + ".tmp")
            shutil.copyfile(staged_file, temporary_target)
            os.replace(temporary_target, target)

    _atomic_write(marker, {"schema": 1, "version": RESOUND_RUNTIME_VERSION, "source": archive_url})
    return {
        "ok": True,
        "installed": True,
        "files": len(installed_paths),
        "source": archive_url,
    }


def _safe_repo_track_path(value: str) -> PurePosixPath:
    raw = str(value or "").replace("\\", "/")
    parts = raw.split("/")
    relative = PurePosixPath(raw)
    if (
        not raw
        or len(parts) != 2
        or any(part in ("", ".", "..") or part.startswith(".") for part in parts)
        or relative.is_absolute()
        or ":" in parts[0]
        or "\x00" in raw
    ):
        raise ValueError(f"Unsafe radio track path: {value!r}")
    for part in parts:
        stem = part.split(".", 1)[0].casefold()
        if stem in {"con", "prn", "aux", "nul"} or re.fullmatch(r"(com|lpt)[1-9]", stem):
            raise ValueError(f"Windows-reserved radio track path: {value!r}")
    if Path(parts[1]).suffix.casefold() not in SUPPORTED_AUDIO_SUFFIXES:
        raise ValueError(f"Unsupported audio format in radio repository: {value!r}")
    return relative


def _git_blob_sha_bytes(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _git_blob_sha_file(path: Path) -> str:
    size = path.stat().st_size
    h = hashlib.sha1(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("schema") == STATE_SCHEMA and isinstance(value.get("files"), list):
            return value
    except (OSError, ValueError, TypeError):
        pass
    return {"schema": STATE_SCHEMA, "files": []}


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _public_url(url: str, allow_local_http: bool = False) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() == "https" and parsed.netloc:
        return url
    if allow_local_http and parsed.scheme.lower() == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.port:
        return url
    raise ValueError("Radio sync only accepts HTTPS GitHub URLs (HTTP is allowed for local tests).")





def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _make_lederg_radio_icon(round_icon: bool = False) -> bytes:
    width = height = 128
    raw = bytearray()
    for y in range(height):
        row = bytearray()
        for x in range(width):
            diagonal = ((x + y * 2) % 44) < 5
            if diagonal:
                red, green, blue = (54, 6, 16)
            else:
                red, green, blue = (5, 5, 8)

            # LEDERG monogram: a white angular L and a red slash.
            if (34 <= x <= 49 and 24 <= y <= 98) or (34 <= x <= 94 and 82 <= y <= 98):
                red, green, blue = (238, 238, 244)
            if 58 <= x <= 93 and 22 <= y <= 42 and (x - 58) >= (42 - y) * 0.8:
                red, green, blue = (245, 24, 48)
            if x < 4 or x >= 124 or y < 4 or y >= 124:
                red, green, blue = (245, 24, 48)

            alpha = 255
            if round_icon and (x - 63.5) ** 2 + (y - 63.5) ** 2 > 60.5 ** 2:
                alpha = 0
            row.extend((red, green, blue, alpha))
        raw.append(0)  # PNG filter type: None
        raw.extend(row)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
        + _png_chunk(b"IEND", b"")
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sync_generated_radio_icons(radio_root: Path, old_state: dict, active_stations: set[str]) -> list[dict]:
    old_records = {}
    for record in old_state.get("generated_icons", []):
        if not isinstance(record, dict) or not isinstance(record.get("station"), str):
            continue
        try:
            station = _safe_repo_track_path(f"{record['station']}/placeholder.mp3").parts[0]
        except ValueError:
            continue
        files = record.get("files", {})
        if isinstance(files, dict):
            old_records[station.casefold()] = {
                name: str(sha).lower()
                for name, sha in files.items()
                if name in {"icon.png", "icon_round.png"} and isinstance(sha, str)
            }

    active_keys = {station.casefold() for station in active_stations}
    # If a station disappeared from GitHub, remove only the default icons that
    # still match our recorded hashes. User-customized icons are left untouched.
    for station_key, file_hashes in old_records.items():
        if station_key in active_keys:
            continue
        station = next(
            (record.get("station") for record in old_state.get("generated_icons", [])
             if isinstance(record, dict) and isinstance(record.get("station"), str)
             and record["station"].casefold() == station_key),
            "",
        )
        try:
            safe_station = _safe_repo_track_path(f"{station}/placeholder.mp3").parts[0]
            station_dir = radio_root / safe_station
            station_dir.resolve().relative_to(radio_root.resolve())
        except (ValueError, OSError):
            continue
        for filename, expected_hash in file_hashes.items():
            target = station_dir / filename
            try:
                target.resolve().relative_to(radio_root.resolve())
                if target.is_file() and not target.is_symlink() and _sha256_file(target) == expected_hash:
                    target.unlink()
            except (OSError, ValueError):
                continue

    generated = []
    for station in sorted(active_stations, key=str.casefold):
        station_dir = radio_root / station
        station_dir.mkdir(parents=True, exist_ok=True)
        try:
            station_dir.resolve().relative_to(radio_root.resolve())
        except ValueError as exc:
            raise ValueError(f"Radio station directory resolves outside radio root: {station}") from exc
        prior = old_records.get(station.casefold(), {})
        files = {}
        for filename, round_icon in (("icon.png", False), ("icon_round.png", True)):
            target = station_dir / filename
            if target.is_symlink():
                continue
            if target.is_file():
                current_hash = _sha256_file(target)
                if prior.get(filename) == current_hash:
                    files[filename] = current_hash
                # Existing files not matching our recorded icon are user-owned.
                continue
            content = _make_lederg_radio_icon(round_icon=round_icon)
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_bytes(content)
            os.replace(temporary, target)
            files[filename] = hashlib.sha256(content).hexdigest()
        if files:
            generated.append({"station": station, "files": files})
    return generated



def _archive_inactive_managed_station_dirs(
    radio_root: Path,
    old_state: dict,
    active_stations: set[str],
) -> list[str]:
    managed = {}
    for record in old_state.get("files", []):
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            continue
        try:
            station = _safe_repo_track_path(record["path"]).parts[0]
        except ValueError:
            continue
        managed.setdefault(station.casefold(), station)
    for record in old_state.get("generated_icons", []):
        if not isinstance(record, dict) or not isinstance(record.get("station"), str):
            continue
        try:
            station = _safe_repo_track_path(f"{record['station']}/placeholder.mp3").parts[0]
        except ValueError:
            continue
        managed.setdefault(station.casefold(), station)

    active_keys = {station.casefold() for station in active_stations}
    archived = []
    radio_root_resolved = radio_root.resolve()
    backup_root = radio_root.parent / "radio-local-backup"
    for station_key, station in sorted(managed.items(), key=lambda item: item[1].casefold()):
        if station_key in active_keys:
            continue
        station_dir = radio_root / station
        if not station_dir.is_dir() or station_dir.is_symlink():
            continue
        try:
            station_dir.resolve().relative_to(radio_root_resolved)
        except ValueError:
            continue
        try:
            if not any(station_dir.iterdir()):
                continue
        except OSError:
            continue

        backup_root.mkdir(parents=True, exist_ok=True)
        destination = backup_root / station
        suffix = 2
        while destination.exists():
            destination = backup_root / f"{station} (archived {suffix})"
            suffix += 1
        shutil.move(str(station_dir), str(destination))
        archived.append(destination.relative_to(radio_root.parent).as_posix())
    return archived

def _get_index(client_http, index_url: str) -> list[dict]:
    response = client_http.get(index_url, timeout=(10, 30))
    response.raise_for_status()
    payload = response.json()
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != 1
        or payload.get("repository") != RADIO_REPOSITORY
        or payload.get("branch") != RADIO_BRANCH
        or not isinstance(payload.get("stations"), list)
        or not isinstance(payload.get("files"), list)
    ):
        raise RuntimeError("GitHub returned an invalid LEDERG radio index.")

    stations = payload["stations"]
    normalized_stations = []
    station_case_map = {}
    for station_value in stations:
        if not isinstance(station_value, str):
            raise ValueError("Radio index station names must be strings.")
        station = station_value.strip()
        _safe_repo_track_path(f"{station}/placeholder.mp3")
        key = station.casefold()
        if key in station_case_map and station_case_map[key] != station:
            raise ValueError(
                f"Station folder names collide on Windows: {station_case_map[key]!r} and {station!r}"
            )
        station_case_map[key] = station
        normalized_stations.append(station)
    if normalized_stations != sorted(normalized_stations, key=str.casefold):
        raise ValueError("Radio index station names are not sorted canonically.")

    result = []
    seen = set()
    for item in payload["files"]:
        if not isinstance(item, dict):
            raise ValueError("A radio index track entry must be an object.")
        relative = _safe_repo_track_path(item.get("path", ""))
        path = relative.as_posix()
        station = str(item.get("station", ""))
        if station != relative.parts[0] or station not in normalized_stations:
            raise ValueError(f"Radio index station does not match track path: {path}")
        key = path.casefold()
        if key in seen:
            raise ValueError(f"Duplicate case-insensitive radio track path: {path}")
        seen.add(key)
        try:
            size = int(item.get("size", -1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid radio track size: {path}") from exc
        sha = str(item.get("sha", "")).lower()
        if size <= 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError(f"Invalid radio track size or Git blob SHA: {path}")
        result.append({"path": path, "station": station, "size": size, "sha": sha})

    if result != sorted(result, key=lambda item: item["path"].casefold()):
        raise ValueError("Radio index tracks are not sorted canonically.")
    canonical = {"stations": normalized_stations, "files": result}
    expected_fingerprint = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    if str(payload.get("fingerprint", "")).lower() != expected_fingerprint:
        raise ValueError("Radio index fingerprint does not match its station and track list.")
    return result


def _get_tree(client_http, tree_url: str) -> list[dict]:
    response = client_http.get(tree_url, timeout=(10, 30))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("tree"), list):
        raise RuntimeError("GitHub returned an invalid radio repository tree.")
    if payload.get("truncated"):
        raise RuntimeError("Radio repository tree is truncated; refusing to delete stations using a partial listing.")
    result = []
    seen_tracks = set()
    station_case_map = {}
    for item in payload["tree"]:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = str(item.get("path", ""))
        parts = path.replace("\\", "/").split("/")
        if len(parts) != 2:
            continue
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"Unsafe station or track path in GitHub tree: {path!r}")
        if parts[0].startswith("."):
            continue
        if Path(parts[1]).suffix.casefold() not in SUPPORTED_AUDIO_SUFFIXES:
            continue
        relative = _safe_repo_track_path(path)
        station = relative.parts[0]
        station_key = station.casefold()
        if station_key in station_case_map and station_case_map[station_key] != station:
            raise ValueError(f"Station folder names collide on Windows: {station_case_map[station_key]!r} and {station!r}")
        station_case_map[station_key] = station
        key = relative.as_posix().casefold()
        if key in seen_tracks:
            raise ValueError(f"Duplicate case-insensitive track path in radio repository: {path}")
        seen_tracks.add(key)
        try:
            size = int(item.get("size", -1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid size in radio repository tree: {path}") from exc
        sha = str(item.get("sha", "")).lower()
        if size <= 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError(f"Invalid Git blob metadata in radio repository: {path}")
        result.append({"path": relative.as_posix(), "station": station, "size": size, "sha": sha})
    result.sort(key=lambda item: item["path"].casefold())
    return result


def _download_track(
    client_http,
    url: str,
    target: Path,
    expected_size: int,
    expected_sha: str,
    timeout=(15, 60),
) -> bool:
    """Download one track with resumable partial files and verify the Git blob SHA."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.resolve().relative_to(target.parent.parent.resolve())
    except ValueError as exc:
        raise ValueError(f"Track path escapes the radio directory: {target}") from exc
    part = target.with_name(target.name + ".lederg.part")

    if target.is_file() and target.stat().st_size == expected_size:
        if _git_blob_sha_file(target) == expected_sha:
            return False
    if part.is_file() and part.stat().st_size > expected_size:
        part.unlink()

    last_error = None
    for attempt in range(4):
        offset = part.stat().st_size if part.is_file() else 0
        if offset > expected_size:
            part.unlink(missing_ok=True)
            offset = 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with client_http.get(url, headers=headers, stream=True, timeout=timeout) as response:
                if offset and response.status_code == 206:
                    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""), re.I)
                    if not match or int(match.group(1)) != offset or int(match.group(3)) != expected_size:
                        raise RuntimeError("GitHub returned an invalid Range response for a radio track.")
                    mode = "ab"
                else:
                    response.raise_for_status()
                    if response.status_code == 206:
                        match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""), re.I)
                        if not match or int(match.group(1)) != 0 or int(match.group(3)) != expected_size:
                            raise RuntimeError("GitHub returned an invalid partial response for a radio track.")
                    offset = 0
                    mode = "wb"

                received = offset
                with part.open(mode) as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        received += len(chunk)
                        stream.write(chunk)
            if received != expected_size or part.stat().st_size != expected_size:
                raise ConnectionError(f"Partial radio track received ({part.stat().st_size}/{expected_size} bytes).")
            if _git_blob_sha_file(part) != expected_sha:
                part.unlink(missing_ok=True)
                raise RuntimeError("GitHub blob SHA mismatch for a radio track.")
            os.replace(part, target)
            return True
        except Exception as exc:
            last_error = exc
            if part.is_file() and part.stat().st_size > expected_size:
                part.unlink(missing_ok=True)
            if attempt < 3:
                continue
    raise RuntimeError(f"Could not download radio track {target.name}: {last_error}") from last_error


def sync_radio_repository(
    game_root: Path,
    http=None,
    tree_url: str | None = None,
    raw_base_url: str | None = None,
    index_url: str | None = None,
) -> dict:
    """
    Sync station folders from the public GitHub repository.

    A top-level folder becomes a station if it contains at least one MP3/OGG/WAV.
    Only files recorded as LEDERG-managed are ever removed on a later sync.
    """
    client_http = http or requests.Session()
    if hasattr(client_http, "headers"):
        client_http.headers.update({"User-Agent": USER_AGENT})
    raw_base_url = _public_url(raw_base_url or RAW_BASE_URL, allow_local_http=True).rstrip("/") + "/"
    if tree_url:
        tree = _get_tree(client_http, _public_url(tree_url, allow_local_http=True))
    else:
        index_url = _public_url(index_url or RADIO_INDEX_URL, allow_local_http=True)
        tree = _get_index(client_http, index_url)
    radio_root = (Path(game_root) / RADIO_RELATIVE_ROOT).resolve()
    game_root_resolved = Path(game_root).resolve()
    try:
        radio_root.relative_to(game_root_resolved)
    except ValueError as exc:
        raise ValueError("Radio directory resolves outside the client root.") from exc
    state_path = radio_root.parent / RADIO_STATE_FILENAME
    old_state = _load_state(state_path)
    old_files = {}
    for item in old_state.get("files", []):
        if isinstance(item, dict):
            try:
                rel = _safe_repo_track_path(item.get("path", "")).as_posix()
            except ValueError:
                continue
            old_files[rel.casefold()] = item

    expected = {item["path"].casefold(): item for item in tree}
    downloaded = 0
    skipped = 0
    failures = []
    local_records = []

    for item in tree:
        relative = _safe_repo_track_path(item["path"])
        target = radio_root.joinpath(*relative.parts)
        try:
            target.resolve().relative_to(radio_root)
        except ValueError as exc:
            raise ValueError(f"Radio track resolves outside radio root: {item['path']}") from exc

        old = old_files.get(item["path"].casefold())
        try:
            if (
                old
                and str(old.get("sha", "")).lower() == item["sha"]
                and int(old.get("size", -1)) == item["size"]
                and target.is_file()
                and target.stat().st_size == item["size"]
                and int(old.get("mtime_ns", -1)) == target.stat().st_mtime_ns
            ):
                changed = False
            else:
                encoded = "/".join(quote(segment, safe="") for segment in relative.parts)
                changed = _download_track(
                    client_http,
                    raw_base_url + encoded,
                    target,
                    item["size"],
                    item["sha"],
                )
            if changed:
                downloaded += 1
            else:
                skipped += 1
            info = target.stat()
            local_records.append({
                "path": item["path"],
                "station": item["station"],
                "sha": item["sha"],
                "size": item["size"],
                "mtime_ns": info.st_mtime_ns,
            })
        except Exception as exc:
            failures.append(f"{item['path']}: {exc}")

    # Do not prune any previously managed content if even one current track failed.
    if failures:
        return {
            "ok": False, "stations": sorted({item["station"] for item in tree}, key=str.casefold),
            "downloaded": downloaded, "skipped": skipped, "removed": [],
            "tracks": len(tree), "errors": failures,
        }

    current = {item["path"].casefold() for item in tree}
    removed = []
    for key, old in old_files.items():
        path_text = str(old.get("path", ""))
        if key in current:
            continue
        try:
            relative = _safe_repo_track_path(path_text)
            target = radio_root.joinpath(*relative.parts)
            target.resolve().relative_to(radio_root)
            if target.is_file() and not target.is_symlink():
                target.unlink()
                removed.append(path_text)
            part = target.with_name(target.name + ".lederg.part")
            if part.is_file() and not part.is_symlink():
                part.unlink()
        except (OSError, ValueError):
            continue

    stations = sorted({item["station"] for item in tree}, key=str.casefold)
    generated_icons = _sync_generated_radio_icons(radio_root, old_state, set(stations))
    archived_local_files = _archive_inactive_managed_station_dirs(radio_root, old_state, set(stations))

    for directory, subdirs, files in os.walk(radio_root, topdown=False):
        folder = Path(directory)
        for name in files:
            path = folder / name
            if path.name.endswith(".lederg.part") and path.is_file():
                # Interrupted partials for still-current tracks remain resumable.
                rel = path.relative_to(radio_root).as_posix()
                if rel.removesuffix(".lederg.part").casefold() not in current:
                    path.unlink(missing_ok=True)
        if folder != radio_root:
            try:
                folder.rmdir()
            except OSError:
                pass

    _atomic_write(state_path, {
        "schema": STATE_SCHEMA,
        "repository": RADIO_REPOSITORY,
        "branch": RADIO_BRANCH,
        "files": local_records,
        "generated_icons": generated_icons,
    })
    return {
        "ok": True,
        "stations": stations,
        "downloaded": downloaded,
        "skipped": skipped,
        "removed": removed,
        "archived_local_files": archived_local_files,
        "tracks": len(tree),
        "errors": [],
    }
