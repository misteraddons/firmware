import json
import tempfile
import unittest
from pathlib import Path

from tools.export_firmware_dashboard import ROOT, build_manifest
from tools.publish_firmware_dashboard import FILES, export


class FirmwareDashboardManifestTests(unittest.TestCase):
    def test_manifest_is_derived_from_every_catalog_item(self):
        catalog = json.loads((ROOT / "firmware_catalog.json").read_text(encoding="utf-8"))
        manifest = build_manifest(ROOT)
        self.assertEqual({item["id"] for item in catalog["items"]}, {item["id"] for item in manifest["products"]})

    def test_only_queryable_approved_product_is_actionable(self):
        manifest = build_manifest(ROOT)
        actionable = [product for product in manifest["products"] if product["identity"].get("supported")]
        self.assertEqual(["reflex-prism"], [product["id"] for product in actionable])
        self.assertTrue(actionable[0]["releases"])

    def test_manifest_never_embeds_firmware_or_secrets(self):
        encoded = json.dumps(build_manifest(ROOT))
        self.assertNotIn("github_token", encoded.casefold())
        self.assertNotIn("data:application", encoded.casefold())
        for product in build_manifest(ROOT)["products"]:
            for release in product["releases"]:
                self.assertRegex(release["url"], r"^https://raw\.githubusercontent\.com/misteraddons/firmware/[0-9a-f]{40}/")

    def test_prism_preserves_hardware_and_protected_range_gates(self):
        prism = next(product for product in build_manifest(ROOT)["products"] if product["id"] == "reflex-prism")
        groups = {target["group"] for target in prism["hardwareCheck"]["acceptedTargets"]}
        self.assertEqual({"prism-v11", "prism-v12", "prism-v13"}, groups)
        self.assertEqual("prism-pro", prism["hardwareCheck"]["knownMismatches"][0]["group"])
        self.assertEqual(0x101F0000, prism["flashPolicy"]["protectedFlashRanges"][0]["start"])
        self.assertFalse(prism["backups"]["settings"]["supported"])
        self.assertTrue(prism["backups"]["settings"]["implemented"])
        latest = next(release for release in prism["releases"] if release["version"] == "1.11")
        self.assertEqual({"prism-v11", "prism-v12", "prism-v13"}, set(latest["hardwareGroups"]))

    def test_docs_export_is_scoped_noindex_and_contains_no_firmware_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "docs").mkdir()
            (repo / "docs" / "_headers").write_text("/*\n  X-Robots-Tag: noindex\n", encoding="utf-8")
            export(repo)
            exported = repo / "docs" / "tools" / "firmware"
            self.assertEqual(set(FILES), {path.name for path in exported.iterdir()})
            self.assertFalse(any(path.suffix.casefold() in {".uf2", ".bin", ".hex"} for path in exported.iterdir()))
            headers = (repo / "docs" / "_headers").read_text(encoding="utf-8")
            self.assertIn("/tools/firmware/*", headers)
            self.assertIn("no-transform", headers)

    def test_dashboard_uses_shared_reflex_theme(self):
        html = (ROOT / "web" / "firmware-dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/stylesheets/reflex.css"', html)
        self.assertIn('class="rx-app" data-reflex-ui', html)
        self.assertIn("rx-button", html)


if __name__ == "__main__":
    unittest.main()
