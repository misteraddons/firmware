import json
import unittest

from tools.sync_prism_firmware import (
    mirror_local_path,
    prism_hardware_check_for_release,
    update_catalog_text,
    update_checksums_text,
    update_readme_text,
)


class PrismFirmwareSyncTests(unittest.TestCase):
    def test_mirror_local_path_uses_existing_prism_layout(self):
        self.assertEqual(mirror_local_path("v1.10.6"), "reflex-prism/v1.10.6/prism_dac.uf2")

    def test_update_catalog_uses_items_key(self):
        catalog = {
            "items": [
                {"id": "other", "local_paths": ["other/file.uf2"]},
                {"id": "reflex-prism", "local_paths": ["reflex-prism/v1.10.5/prism_dac.uf2"]},
            ]
        }

        updated = json.loads(update_catalog_text(json.dumps(catalog), "v1.10.6"))

        self.assertEqual(updated["items"][1]["local_paths"], ["reflex-prism/v1.10.6/prism_dac.uf2"])

    def test_prism_hardware_check_detects_v11_release(self):
        check = prism_hardware_check_for_release("v1.10.7", "Prism V1.1 Firmware v1.10.7")

        self.assertEqual(check["expected_group"], "prism-v11")
        self.assertEqual(check["expected_label"], "Prism V1.05/V1.1")
        self.assertIn("Hardware target: V1.05/V1.1 boards", check["expect"])

    def test_prism_hardware_check_detects_v12_release(self):
        check = prism_hardware_check_for_release("v1.20.0", "Prism V1.2 Firmware v1.20.0")

        self.assertEqual(check["expected_group"], "prism-v12")
        self.assertEqual(check["expected_label"], "Prism V1.2")
        self.assertIn("Hardware target: V1.2 boards", check["expect"])

    def test_update_checksums_adds_new_prism_file_without_dropping_old_versions(self):
        text = "\n".join(
            [
                "aaa  reflex-prism/v1.10.5/prism_dac.uf2",
                "bbb  reflex-ctrl/v0.7.12/controller.uf2",
            ]
        )

        updated = update_checksums_text(text, "v1.10.6", "ccc").splitlines()

        self.assertIn("aaa  reflex-prism/v1.10.5/prism_dac.uf2", updated)
        self.assertIn("bbb  reflex-ctrl/v0.7.12/controller.uf2", updated)
        self.assertIn("ccc  reflex-prism/v1.10.6/prism_dac.uf2", updated)

    def test_update_readme_replaces_table_row_and_prism_note(self):
        readme = "\n".join(
            [
                "| Project | Local file | Source |",
                "| --- | --- | --- |",
                "| Reflex Prism | `reflex-prism/v1.10.5/prism_dac.uf2` | old source |",
                "",
                "Reflex Prism: use `prism_dac.uf2` for the Prism firmware update. Old note.",
            ]
        )

        updated = update_readme_text(readme, "v1.10.6")

        self.assertIn(
            "| Reflex Prism | `reflex-prism/v1.10.6/prism_dac.uf2` | [`misteraddons/Reflex-Prism` v1.10.6](https://github.com/misteraddons/Reflex-Prism/releases/tag/v1.10.6) |",
            updated,
        )
        self.assertIn("The latest mirrored release is `v1.10.6`.", updated)
        self.assertNotIn("Old note", updated)


if __name__ == "__main__":
    unittest.main()
