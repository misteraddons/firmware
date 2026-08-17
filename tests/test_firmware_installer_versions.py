import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import firmware_installer as installer


class DownloadResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()


class FirmwareInstallerVersionTests(unittest.TestCase):
    def test_prism_versions_include_bundled_history_with_latest_first(self):
        item = installer.get_catalog_item("reflex-prism")

        versions = installer.catalog_firmware_versions(item)

        self.assertEqual("prism-v1-r10", versions[0].version)
        self.assertIn("v1.10.9", [version.version for version in versions])
        self.assertNotIn("current", [version.version for version in versions])

    def test_legacy_prism_version_is_limited_to_v11_hardware(self):
        item = installer.get_catalog_item("reflex-prism")

        version = installer.select_catalog_firmware_version(item, "v1.10.9")

        self.assertIsNotNone(version.hardware_check)
        assert version.hardware_check is not None
        self.assertEqual("prism-v11", version.hardware_check["expected_group"])
        self.assertEqual(["Hardware target: V1.05/V1.1 boards"], version.hardware_check["expect"])
        mismatch_groups = {entry["group"] for entry in version.hardware_check["known_mismatches"]}
        self.assertIn("prism-v12", mismatch_groups)
        self.assertIn("prism-v13", mismatch_groups)

    def test_unified_prism_release_keeps_all_supported_v1_targets(self):
        item = installer.get_catalog_item("reflex-prism")

        version = installer.select_catalog_firmware_version(item, "prism-v1-r10")

        self.assertIsNotNone(version.hardware_check)
        assert version.hardware_check is not None
        groups = {entry["group"] for entry in version.hardware_check["accepted_targets"]}
        self.assertEqual({"prism-v11", "prism-v12", "prism-v13"}, groups)

    def test_download_validation_failure_preserves_existing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "firmware.uf2"
            target.write_bytes(b"known-good")

            def reject(_path: Path) -> None:
                raise installer.FirmwareError("invalid download")

            with patch.object(installer.urllib.request, "urlopen", return_value=DownloadResponse(b"invalid")):
                with self.assertRaisesRegex(installer.FirmwareError, "invalid download"):
                    installer.download_file("https://example.invalid/firmware.uf2", target, validate=reject)

            self.assertEqual(b"known-good", target.read_bytes())
            self.assertFalse(target.with_name("firmware.tmp.uf2").exists())

    def test_refresh_skips_download_when_immutable_version_is_bundled(self):
        plan = installer.DownloadPlan(
            url="https://example.invalid/prism_dac.uf2",
            file_name="prism_dac.uf2",
            source_label="test",
            version="prism-v1-r10",
            immutable_version=True,
        )

        with (
            patch.object(installer, "resolve_download_plan", return_value=plan),
            patch.object(installer, "_download_catalog_plan") as download,
        ):
            firmware = installer.refresh_catalog_firmware("reflex-prism")

        self.assertEqual("prism_dac.uf2", firmware.path.name)
        download.assert_not_called()

    def test_mutable_source_uses_catalog_version_label_for_cache(self):
        item = installer.get_catalog_item("mistercade-v1-2025")
        plan = installer.DownloadPlan(
            url="https://example.invalid/package.zip",
            file_name="package.zip",
            source_label="test",
            version="main",
        )

        self.assertEqual("main-2025", installer._cache_version_for_plan(item, plan))


if __name__ == "__main__":
    unittest.main()
