import unittest

from tools.audit_firmware_sources import SourceCheck, resolve_check_local_path


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


if __name__ == "__main__":
    unittest.main()
