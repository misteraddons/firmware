import json
import tempfile
import unittest
from pathlib import Path

from tools.export_firmware_dashboard import ROOT, build_manifest
from tools.publish_firmware_dashboard import FILES, check, export


class FirmwareDashboardManifestTests(unittest.TestCase):
    def test_manifest_is_derived_from_every_catalog_item(self):
        catalog = json.loads((ROOT / "firmware_catalog.json").read_text(encoding="utf-8"))
        manifest = build_manifest(ROOT)
        self.assertEqual({item["id"] for item in catalog["items"]}, {item["id"] for item in manifest["products"]})

    def test_only_approved_products_are_queryable(self):
        manifest = build_manifest(ROOT)
        actionable = [product for product in manifest["products"] if product["identity"].get("supported")]
        self.assertEqual({"reflex-adapt-classic2usb", "reflex-prism"}, {product["id"] for product in actionable})
        classic = next(product for product in actionable if product["id"] == "reflex-adapt-classic2usb")
        self.assertEqual("serial", classic["identity"]["transport"])
        self.assertEqual("classic2usb-management-v1", classic["identity"]["protocol"])
        self.assertEqual("IDENTITY", classic["identity"]["identityCommand"])
        self.assertEqual(["Classic2USB"], classic["identity"]["hidProductIds"])
        self.assertFalse(classic["releases"])

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

    def test_dashboard_exposes_identity_release_transport_and_manual_states(self):
        html = (ROOT / "web" / "firmware-dashboard" / "index.html").read_text(encoding="utf-8")
        for element_id in ("product-status", "identity-status", "release-status", "flash-status", "show-manual", "product-picker"):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("Manual fallback — not identity-verified", html)
        # The catalog is no longer Classic2USB-only, so the manual step warns about
        # using the wrong image generally rather than naming one product.
        self.assertIn("Use only the validated file", html)
        self.assertIn("share the RP2040 family and will copy without complaint", html)

    def test_manual_selection_is_labelled_as_unverified(self):
        html = (ROOT / "web" / "firmware-dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn("nothing verifies the hardware", html)
        core = (ROOT / "web" / "firmware-dashboard" / "core.js").read_text(encoding="utf-8")
        # A declared product must never be able to present itself as verified.
        self.assertIn("identitySupport: 'declared'", core)
        self.assertIn("uniqueId: null", core)



class FirmwareDashboardDriftTests(unittest.TestCase):
    """The export is one-directional, so drift has to be detectable."""

    @staticmethod
    def _docs_repo(root: str) -> Path:
        repo = Path(root)
        (repo / "docs").mkdir(parents=True)
        (repo / "docs" / "_headers").write_text("/*\n  X-Frame-Options: DENY\n", encoding="utf-8")
        return repo

    def test_a_freshly_exported_docs_repo_reports_no_drift(self):
        with tempfile.TemporaryDirectory() as name:
            repo = self._docs_repo(name)
            export(repo)
            self.assertEqual(check(repo), [])

    def test_an_unexported_docs_repo_reports_every_missing_file(self):
        with tempfile.TemporaryDirectory() as name:
            repo = self._docs_repo(name)
            drift = check(repo)
            self.assertEqual(len([d for d in drift if d.startswith("absent")]), len(FILES) + 1)

    def test_an_edited_deployed_file_is_detected(self):
        with tempfile.TemporaryDirectory() as name:
            repo = self._docs_repo(name)
            export(repo)
            target = repo / "docs" / "tools" / "firmware" / "app.js"
            target.write_text(target.read_text(encoding="utf-8") + "\n// drifted\n", encoding="utf-8")
            self.assertIn("deployed copy differs: app.js", check(repo))

    def test_a_stale_deployed_manifest_is_detected(self):
        with tempfile.TemporaryDirectory() as name:
            repo = self._docs_repo(name)
            export(repo)
            target = repo / "docs" / "tools" / "firmware" / "manifest.json"
            manifest = json.loads(target.read_text(encoding="utf-8"))
            manifest["products"][0]["releases"] = []
            target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            self.assertIn("deployed copy differs: manifest.json", check(repo))


if __name__ == "__main__":
    unittest.main()
