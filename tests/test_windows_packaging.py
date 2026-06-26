import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLED_FIRMWARE_PATHS = (
    "mistercade-v1",
    "mistercade-v2",
    "reflex-adapt",
    "reflex-ctrl",
    "reflex-encode",
    "reflex-prism",
)


class WindowsPackagingTests(unittest.TestCase):
    def test_windows_build_bundles_catalog_checksums_and_firmware_mirrors(self):
        script = (ROOT / "build_windows_exe.ps1").read_text(encoding="utf-8")

        self.assertIn("$RepoRoot\\firmware_catalog.json;.", script)
        self.assertIn("$RepoRoot\\checksums.sha256;.", script)
        for path in BUNDLED_FIRMWARE_PATHS:
            self.assertIn(f"$RepoRoot\\{path};{path}", script)

    def test_windows_build_uses_configurable_tk_capable_python(self):
        script = (ROOT / "build_windows_exe.ps1").read_text(encoding="utf-8")

        self.assertIn("FIRMWARE_INSTALLER_PYTHON", script)
        self.assertIn("import tkinter", script)
        self.assertIn("& $PythonExe -m PyInstaller", script)


if __name__ == "__main__":
    unittest.main()
