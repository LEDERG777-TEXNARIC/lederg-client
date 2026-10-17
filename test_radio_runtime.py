import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from radio_sync import ensure_resound_runtime


def make_runtime_zip(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError("HTTP error")


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        return FakeResponse(self.payload)


class RadioRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "GTA Criminal Russia"
        self.root.mkdir()
        self.payload = make_runtime_zip({
            "resound_SA.asi": b"plugin-binary",
            "bass.dll": b"bass-dependency",
            "resound/fonts/Bank_Gothic_Medium.ttf": b"font",
            "resound/sounds/radiosystem/radioselect.mp3": b"click",
            "resound/textures/radio_icons/1.png": b"icon",
            "resound/icons_util/img_funx.exe": b"helper",
            "README.txt": b"not installed",
        })
        self.http = FakeHttp(self.payload)
        self.url = "https://github.com/true-goniss/resound-SA/releases/download/1.2-alpha/resound_SA.v1.2-alpha.zip"

    def tearDown(self):
        self.temp.cleanup()

    def test_downloads_official_radio_runtime_and_required_assets(self):
        result = ensure_resound_runtime(self.root, http=self.http, archive_url=self.url)
        self.assertTrue(result["ok"])
        self.assertTrue(result["installed"])
        self.assertEqual((self.root / "resound_SA.asi").read_bytes(), b"plugin-binary")
        self.assertEqual((self.root / "bass.dll").read_bytes(), b"bass-dependency")
        self.assertEqual(
            (self.root / "resound" / "textures" / "radio_icons" / "1.png").read_bytes(),
            b"icon",
        )
        self.assertFalse((self.root / "README.txt").exists())
        self.assertEqual(len(self.http.calls), 1)

    def test_complete_runtime_does_not_download_again(self):
        for relative, data in {
            "resound_SA.asi": b"plugin",
            "bass.dll": b"bass",
            "resound/fonts/Bank_Gothic_Medium.ttf": b"font",
            "resound/sounds/radiosystem/radioselect.mp3": b"click",
            "resound/textures/radio_icons/1.png": b"icon",
            "resound/icons_util/img_funx.exe": b"helper",
        }.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        result = ensure_resound_runtime(self.root, http=self.http, archive_url=self.url)
        self.assertTrue(result["ok"])
        self.assertFalse(result["installed"])
        self.assertEqual(self.http.calls, [])

    def test_runtime_version_mismatch_triggers_reinstall(self):
        import json
        for relative, data in {
            "resound_SA.asi": b"old-plugin",
            "bass.dll": b"old-bass",
            "resound/fonts/Bank_Gothic_Medium.ttf": b"font",
            "resound/sounds/radiosystem/radioselect.mp3": b"click",
            "resound/textures/radio_icons/1.png": b"icon",
            "resound/icons_util/img_funx.exe": b"helper",
        }.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        marker = self.root / "resound" / ".lederg-radio-state-runtime.json"
        marker.write_text(json.dumps({"schema": 1, "version": "old-version"}), encoding="utf-8")
        result = ensure_resound_runtime(self.root, http=self.http, archive_url=self.url)
        self.assertTrue(result["ok"] and result["installed"])
        self.assertEqual((self.root / "resound_SA.asi").read_bytes(), b"plugin-binary")
        installed_marker = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(installed_marker["version"], "1.2-alpha")
        self.assertEqual(len(self.http.calls), 1)

    def test_rejects_zip_path_traversal_before_installing_any_file(self):
        self.http.payload = make_runtime_zip({
            "resound_SA.asi": b"plugin-binary",
            "bass.dll": b"bass-dependency",
            "../outside.txt": b"escape",
            "resound/fonts/Bank_Gothic_Medium.ttf": b"font",
            "resound/sounds/radiosystem/radioselect.mp3": b"click",
            "resound/textures/radio_icons/1.png": b"icon",
            "resound/icons_util/img_funx.exe": b"helper",
        })
        with self.assertRaises(ValueError):
            ensure_resound_runtime(self.root, http=self.http, archive_url=self.url)
        self.assertFalse((self.root / "resound_SA.asi").exists())
        self.assertFalse((Path(self.temp.name) / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
