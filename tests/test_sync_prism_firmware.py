import json
import unittest

from tools.sync_prism_firmware import (
    FLASH_NUKE_ASSET_NAME,
    latest_asset_path,
    mirror_local_path,
    prism_hardware_check_for_release,
    update_catalog_text,
    update_checksums_text,
    update_readme_text,
)


class PrismFirmwareSyncTests(unittest.TestCase):
    def test_mirror_local_path_uses_existing_prism_layout(self):
        self.assertEqual(mirror_local_path("prism-v1-r10"), "reflex-prism/prism-v1-r10/prism_dac.uf2")

    def test_update_catalog_uses_items_key(self):
        catalog = {
            "items": [
                {"id": "other", "local_paths": ["other/file.uf2"]},
                {"id": "reflex-prism", "local_paths": ["reflex-prism/prism-v1-r9/prism_dac.uf2"]},
            ]
        }

        updated = json.loads(update_catalog_text(json.dumps(catalog), "prism-v1-r10"))

        self.assertEqual(updated["items"][1]["local_paths"], ["reflex-prism/prism-v1-r10/prism_dac.uf2"])
        self.assertEqual(
            updated["items"][1]["sources"][0]["path"],
            "reflex-prism/latest/prism_dac.uf2",
        )

    def test_prism_hardware_check_accepts_unified_v1_release(self):
        check = prism_hardware_check_for_release("prism-v1-r10", "Reflex Prism DAC V1 Release 10")

        self.assertEqual(check["expected_group"], "prism-v1")
        self.assertEqual(check["expected_label"], "Reflex Prism DAC V1")
        markers = [marker for target in check["accepted_targets"] for marker in target["markers"]]
        self.assertIn("Hardware target: V1.05/V1.1 boards", markers)
        self.assertIn("Hardware target: V1.2 boards", markers)
        self.assertIn("Hardware target: V1.3 Smart HD15 boards", markers)

    def test_update_checksums_adds_new_prism_file_without_dropping_old_versions(self):
        text = "\n".join(
            [
                "aaa  reflex-prism/prism-v1-r9/prism_dac.uf2",
                "bbb  reflex-ctrl/v0.7.12/controller.uf2",
            ]
        )

        updated = update_checksums_text(text, "v1.11", "ccc", "ddd").splitlines()

        self.assertIn("aaa  reflex-prism/prism-v1-r9/prism_dac.uf2", updated)
        self.assertIn("bbb  reflex-ctrl/v0.7.12/controller.uf2", updated)
        self.assertIn("ccc  reflex-prism/v1.11/prism_dac.uf2", updated)
        self.assertIn("ccc  reflex-prism/latest/prism_dac.uf2", updated)
        self.assertIn("ddd  reflex-prism/v1.11/flash_nuke.uf2", updated)
        self.assertIn("ddd  reflex-prism/latest/flash_nuke.uf2", updated)

    def test_update_readme_replaces_table_row_and_prism_note(self):
        readme = "\n".join(
            [
                "| Project | Local file | Source |",
                "| --- | --- | --- |",
                "| Reflex Prism | `reflex-prism/prism-v1-r9/prism_dac.uf2` | old source |",
                "",
                "Reflex Prism: use `prism_dac.uf2` for the Prism firmware update. Old note.",
            ]
        )

        updated = update_readme_text(readme, "v1.11")

        self.assertIn(
            "| Reflex Prism | `reflex-prism/latest/prism_dac.uf2` | [`misteraddons/Reflex-Prism` v1.11](https://github.com/misteraddons/Reflex-Prism/releases/tag/v1.11) |",
            updated,
        )
        self.assertIn("| Reflex Prism Flash Nuke | `reflex-prism/latest/flash_nuke.uf2` |", updated)
        self.assertIn("The latest mirrored release is `v1.11`.", updated)
        self.assertIn(latest_asset_path(FLASH_NUKE_ASSET_NAME), updated)
        self.assertNotIn("Old note", updated)


if __name__ == "__main__":
    unittest.main()
