import unittest

from unittest.mock import patch

from tools.audit_firmware_sources import (
    SourceCheck,
    resolve_catalog_release_tag,
    resolve_check_local_path,
)


class FirmwareAuditSourceTests(unittest.TestCase):
    def test_resolves_catalog_backed_check_path(self):
        check = SourceCheck(
            "Reflex Prism",
            "",
            "github_release_asset",
            "misteraddons/Reflex-Prism",
            catalog_item_id="reflex-prism",
        )

        self.assertEqual(
            resolve_check_local_path(check, {"reflex-prism": ["reflex-prism/v1.10.6/prism_dac.uf2"]}),
            "reflex-prism/v1.10.6/prism_dac.uf2",
        )

    def test_catalog_release_tag_supports_prerelease_tags(self):
        with patch(
            "tools.audit_firmware_sources.catalog_local_paths_by_id",
            return_value={"reflex-prism": ["reflex-prism/v1.11/prism_dac.uf2"]},
        ):
            self.assertEqual(resolve_catalog_release_tag("reflex-prism"), "v1.11")


if __name__ == "__main__":
    unittest.main()
