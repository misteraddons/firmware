import json
import unittest

from tools.sync_prism_firmware import (
    FLASH_NUKE_ASSET_NAME,
    latest_asset_path,
    mirror_local_path,
    public_latest_url,
    prism_hardware_check_for_release,
    update_catalog_text,
    update_checksums_text,
    update_readme_text,
)


class PrismFirmwareSyncTests(unittest.TestCase):
    def test_mirror_local_path_uses_existing_prism_layout(self):
        self.assertEqual(mirror_local_path("v1.10.10"), "reflex-prism/v1.10.10/prism_dac.uf2")

    def test_update_catalog_uses_items_key(self):
        catalog = {
            "items": [
                {"id": "other", "local_paths": ["other/file.uf2"]},
                {"id": "reflex-prism", "local_paths": ["reflex-prism/prism-v1-r9/prism_dac.uf2"]},
            ]
        }

        updated = json.loads(update_catalog_text(json.dumps(catalog), "v1.10.10"))

        self.assertEqual(updated["items"][1]["local_paths"], ["reflex-prism/v1.10.10/prism_dac.uf2"])
        self.assertEqual(
            updated["items"][1]["sources"][0]["path"],
            "reflex-prism/latest/prism_dac.uf2",
        )

    def test_prerelease_is_labeled_as_release_candidate(self):
        catalog = {"items": [{"id": "reflex-prism", "local_paths": []}]}
        updated = json.loads(update_catalog_text(json.dumps(catalog), "v1.11", prerelease=True))

        self.assertEqual(updated["items"][0]["label"], "Reflex Prism v1.11 Release Candidate")
        self.assertEqual(updated["items"][0]["release_channel"], "prerelease")

    def test_v11010_hardware_check_is_limited_to_v11_boards(self):
        check = prism_hardware_check_for_release("v1.10.10", "Prism Firmware v1.10.10")

        self.assertEqual(check["expected_group"], "prism-v11")
        self.assertEqual(check["expect"], ["Hardware target: V1.05/V1.1 boards"])
        self.assertNotIn("accepted_targets", check)

    def test_v111_hardware_check_accepts_unified_v1_release(self):
        check = prism_hardware_check_for_release("v1.11", "Prism Firmware v1.11")

        groups = {target["group"] for target in check["accepted_targets"]}
        self.assertEqual({"prism-v11", "prism-v12", "prism-v13"}, groups)

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
            "| Reflex Prism | [`reflex-prism/latest/prism_dac.uf2`](reflex-prism/latest/prism_dac.uf2) | "
            f"[stable download]({public_latest_url('prism_dac.uf2')}) |",
            updated,
        )
        self.assertIn(
            "| Reflex Prism Flash Nuke | "
            "[`reflex-prism/latest/flash_nuke.uf2`](reflex-prism/latest/flash_nuke.uf2) |",
            updated,
        )
        self.assertIn("The latest mirrored release is `v1.11`.", updated)
        self.assertIn(latest_asset_path(FLASH_NUKE_ASSET_NAME), updated)
        self.assertIn(public_latest_url(FLASH_NUKE_ASSET_NAME), updated)
        self.assertNotIn("Old note", updated)

    def test_update_readme_labels_prerelease(self):
        readme = "\n".join(
            [
                "| Reflex Prism | old | old |",
                "",
                "Reflex Prism: use `prism_dac.uf2` for the Prism firmware update. Old note.",
            ]
        )
        updated = update_readme_text(readme, "v1.11", prerelease=True)
        self.assertIn("release candidate download", updated)
        self.assertIn("pending hardware testing", updated)


if __name__ == "__main__":
    unittest.main()
