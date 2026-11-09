import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from scripts import build_mihomo


TEMPLATE = Path(__file__).resolve().parents[1] / "config/routing-template.json"


class BuildMihomoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.geosite = self.root / "geosite"
        self.geoip = self.root / "geoip"
        self.geosite.mkdir()
        self.geoip.mkdir()
        self.cfg = json.loads(TEMPLATE.read_text())
        for category in build_mihomo.categories(self.cfg, "geosite"):
            (self.geosite / f"{category}.txt").write_text("domain:example.org\n")
        for category in build_mihomo.categories(self.cfg, "geoip"):
            (self.geoip / f"{category}.txt").write_text("192.0.2.0/24\n2001:db8::/32\n")

    def build(self):
        return build_mihomo.build(self.cfg, "test/routing", self.geosite, self.geoip)

    def test_profile_preserves_priority_and_only_importable_sections(self):
        profile, payloads = self.build()
        self.assertEqual(set(profile), {"proxy-groups", "rule-providers", "rules"})
        rules = profile["rules"]
        self.assertEqual(rules[0], "RULE-SET,geosite-win-spy,REJECT")
        self.assertLess(rules.index("RULE-SET,geosite-youtube,PROXY"),
                        rules.index("RULE-SET,geosite-russia-inside,DIRECT"))
        self.assertLess(rules.index("RULE-SET,geosite-russia-inside,DIRECT"),
                        rules.index("RULE-SET,geoip-direct,DIRECT"))
        self.assertEqual(rules[-1], "MATCH,PROXY")
        self.assertNotIn("DIRECT", profile["proxy-groups"][0]["proxies"])
        self.assertEqual(profile["proxy-groups"][0]["proxies"], ["AUTO"])
        self.assertTrue(all(g["include-all"] for g in profile["proxy-groups"]))
        for provider in profile["rule-providers"].values():
            filename = provider["url"].removeprefix(
                "https://raw.githubusercontent.com/test/routing/main/MIHOMO/")
            self.assertIn(filename, payloads)
            self.assertEqual(provider["interval"], 86400)
            self.assertEqual(provider["proxy"], "PROXY")
        provider = profile["rule-providers"]["geoip-russia-inside"]
        self.assertEqual(provider["behavior"], "ipcidr")
        self.assertEqual(provider["format"], "text")
        self.assertEqual(payloads["rules/geoip-russia-inside.txt"],
                         "192.0.2.0/24\n2001:db8::/32\n")

    def test_dlc_export_types_attributes_and_regex_survive_yaml(self):
        path = self.geosite / "youtube.txt"
        path.write_text("domain:example.org:@ads,@ru\nfull:exact.example.org\n"
                        "keyword:youtube\nregexp:^https?://video[0-9]+\\.example\n"
                        "domain:example.org:@ru\n")
        _, payloads = self.build()
        payload = yaml.safe_load(payloads["rules/geosite-youtube.yaml"])["payload"]
        self.assertCountEqual(payload, ["DOMAIN-SUFFIX,example.org", "DOMAIN,exact.example.org",
                                       "DOMAIN-KEYWORD,youtube",
                                       "DOMAIN-REGEX,^https?://video[0-9]+\\.example"])

    def test_explicit_rules_and_global_direct(self):
        self.cfg["BlockSites"] = ["full:ads.example.org", "regexp:^ads[0-9]+\\.example"]
        self.cfg["ProxyIp"] = ["192.0.2.5", "2001:db8::1/128"]
        self.cfg["DirectSites"] = ["example.ru"]
        self.cfg["GlobalProxy"] = False
        profile, _ = self.build()
        self.assertIn("DOMAIN,ads.example.org,REJECT", profile["rules"])
        self.assertIn("IP-CIDR,192.0.2.5/32,PROXY", profile["rules"])
        self.assertIn("IP-CIDR6,2001:db8::1/128,PROXY", profile["rules"])
        self.assertIn("DOMAIN-SUFFIX,example.ru,DIRECT", profile["rules"])
        self.assertEqual(profile["rules"][-1], "MATCH,DIRECT")

    def test_invalid_order_or_category_is_not_silently_accepted(self):
        self.cfg["RouteOrder"] = "direct-direct-proxy"
        with self.assertRaisesRegex(ValueError, "RouteOrder"):
            self.build()
        self.cfg["RouteOrder"] = "block-proxy-direct"
        self.cfg["DirectSites"] = ["geosite:google@ads"]
        with self.assertRaisesRegex(ValueError, "категория"):
            self.build()

    def test_missing_category_preserves_existing_output(self):
        outdir = self.root / "MIHOMO"
        (outdir / "rules").mkdir(parents=True)
        files = [outdir / "default.yaml", outdir / "rules/previous.yaml"]
        for path in files:
            path.write_text("previous contents\n")
        (self.geosite / "youtube.txt").unlink()
        with patch("sys.argv", ["build_mihomo.py", "--template", str(TEMPLATE),
                                "--repo", "test/routing", "--geosite-dir", str(self.geosite),
                                "--geoip-dir", str(self.geoip), "--outdir", str(outdir)]):
            with self.assertRaises(FileNotFoundError):
                build_mihomo.main()
        for path in files:
            self.assertEqual(path.read_text(), "previous contents\n")

    def test_generated_file_is_yaml_and_unused_lists_are_removed(self):
        outdir = self.root / "MIHOMO"
        (outdir / "rules").mkdir(parents=True)
        (outdir / "rules/obsolete.yaml").write_text("obsolete")
        argv = ["build_mihomo.py", "--template", str(TEMPLATE), "--repo", "test/routing",
                "--geosite-dir", str(self.geosite), "--geoip-dir", str(self.geoip),
                "--outdir", str(outdir)]
        with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build_mihomo.main(), 0)
            previous = (outdir / "default.yaml").read_bytes()
            self.assertEqual(build_mihomo.main(), 0)
        self.assertEqual(previous, (outdir / "default.yaml").read_bytes())
        profile = yaml.safe_load(previous)
        self.assertEqual(profile["rules"][-1], "MATCH,PROXY")
        self.assertFalse((outdir / "rules/obsolete.yaml").exists())
        self.assertEqual(len(list((outdir / "rules").iterdir())), len(profile["rule-providers"]))


if __name__ == "__main__":
    unittest.main()
