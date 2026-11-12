import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from scripts import build_mihomo


# Тесты не зависят от редактируемых пользователем DNS, категорий и RouteOrder.
FIXTURE = {
    "Name": "Test", "GlobalProxy": "true", "RouteOrder": "block-proxy-direct",
    "RemoteDNSType": "DoH", "RemoteDNSIP": "8.8.8.8", "RemoteDns": "8.8.8.8",
    "RemoteDNSDomain": "https://dns.google/dns-query",
    "DomesticDNSType": "DoH", "DomesticDNSIP": "77.88.8.8", "DomesticDns": "77.88.8.8",
    "DomesticDNSDomain": "https://common.dot.dns.yandex.net/dns-query",
    "DirectSites": ["geosite:private", "geosite:russia-inside", "geosite:microsoft"],
    "ProxySites": ["geosite:youtube", "geosite:github"], "BlockSites": ["geosite:win-spy"],
    "DirectIp": ["geoip:private", "geoip:direct", "geoip:russia-inside"],
    "ProxyIp": [], "BlockIp": [],
}


class BuildMihomoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.geosite = self.root / "geosite"
        self.geoip = self.root / "geoip"
        self.geosite.mkdir()
        self.geoip.mkdir()
        self.cfg = copy.deepcopy(FIXTURE)
        self.template = self.root / "routing-template.json"
        self.template.write_text(json.dumps(self.cfg))
        for category in build_mihomo.categories(self.cfg, "geosite"):
            (self.geosite / f"{category}.txt").write_text("domain:example.org\n")
        for category in build_mihomo.categories(self.cfg, "geoip"):
            (self.geoip / f"{category}.txt").write_text("192.0.2.0/24\n2001:db8::/32\n")

    def build(self):
        return build_mihomo.build(self.cfg, "test/routing", self.geosite, self.geoip)

    def test_profile_preserves_priority_and_only_importable_sections(self):
        profile, payloads = self.build()
        self.assertEqual(set(profile), {"proxy-groups", "rule-providers", "rules", "dns"})
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

    def test_dns_transfers_doh_and_preserves_priority_after_map_sort(self):
        profile, payloads = self.build()
        dns = profile["dns"]
        self.assertEqual(dns["nameserver"], ["https://dns.google/dns-query#PROXY"])
        self.assertEqual(dns["direct-nameserver"],
                         ["https://common.dot.dns.yandex.net/dns-query#DIRECT"])
        self.assertEqual(dns["proxy-server-nameserver"], dns["direct-nameserver"])
        self.assertEqual(dns["default-nameserver"], ["77.88.8.8", "8.8.8.8"])
        self.assertNotIn("fallback", dns)
        # Go map serialization in 3x-ui must not put DIRECT before PROXY.
        policy = dns["nameserver-policy"]
        self.assertEqual(list(policy), sorted(policy))
        self.assertEqual(list(policy), ["rule-set:dns-01-proxy", "rule-set:dns-02-direct"])
        proxy = yaml.safe_load(payloads["rules/dns-01-proxy.yaml"])["payload"]
        direct = yaml.safe_load(payloads["rules/dns-02-direct.yaml"])["payload"]
        self.assertIn("DOMAIN-SUFFIX,example.org", proxy)
        self.assertIn("DOMAIN-SUFFIX,example.org", direct)
        for key in policy:
            self.assertIn(key.removeprefix("rule-set:"), profile["rule-providers"])

    def test_dns_respects_direct_first_order_and_plain_domain_types(self):
        self.cfg["RouteOrder"] = "direct-proxy-block"
        self.cfg["DirectSites"] = ["full:corp.example", "regexp:^intranet[0-9]+\\.example"]
        self.cfg["ProxySites"] = ["keyword:video"]
        self.cfg["GlobalProxy"] = False
        profile, payloads = self.build()
        policy = profile["dns"]["nameserver-policy"]
        self.assertEqual(list(policy), ["rule-set:dns-00-direct", "rule-set:dns-01-proxy"])
        self.assertEqual(profile["dns"]["nameserver"], profile["dns"]["direct-nameserver"])
        direct = yaml.safe_load(payloads["rules/dns-00-direct.yaml"])["payload"]
        self.assertIn("DOMAIN,corp.example", direct)
        self.assertIn("DOMAIN-REGEX,^intranet[0-9]+\\.example", direct)

    def test_family_dns_does_not_gain_unfiltered_content_fallback(self):
        self.cfg.update({"RemoteDNSDomain": "https://family.cloudflare-dns.com/dns-query",
                         "RemoteDNSIP": "1.1.1.3", "RemoteDns": "8.8.8.8",
                         "DomesticDNSDomain": "https://doh.cleanbrowsing.org/doh/family-filter/",
                         "DomesticDNSIP": "185.228.168.168", "DomesticDns": "77.88.8.8"})
        profile, _ = self.build()
        dns = profile["dns"]
        self.assertEqual(dns["default-nameserver"], ["185.228.168.168", "1.1.1.3"])
        self.assertEqual(dns["nameserver"], ["https://family.cloudflare-dns.com/dns-query#PROXY"])
        self.assertEqual(dns["direct-nameserver"],
                         ["https://doh.cleanbrowsing.org/doh/family-filter/#DIRECT"])
        self.assertNotIn("8.8.8.8", json.dumps(dns))
        self.assertNotIn("77.88.8.8", json.dumps(dns))
        self.assertNotIn("fallback", dns)

    def test_legacy_dns_fields_and_absent_dns_remain_supported(self):
        for prefix in ("Remote", "Domestic"):
            for suffix in ("DNSType", "DNSDomain", "DNSIP"):
                self.cfg.pop(prefix + suffix, None)
        profile, _ = self.build()
        self.assertEqual(profile["dns"]["nameserver"], ["udp://8.8.8.8#PROXY"])
        self.assertEqual(profile["dns"]["direct-nameserver"], ["udp://77.88.8.8#DIRECT"])
        self.cfg.pop("RemoteDns");self.cfg.pop("DomesticDns")
        profile, payloads = self.build()
        self.assertNotIn("dns", profile)
        self.assertFalse(any(name.startswith("rules/dns-") for name in payloads))

    def test_invalid_dns_does_not_silently_fall_back_to_plaintext(self):
        for change in [{"RemoteDNSType": "typo"},
                       {"RemoteDNSType": "", "RemoteDNSDomain": "http://dns.google/dns-query"},
                       {"RemoteDNSType": "", "RemoteDNSDomain": "", "RemoteDns": "http://dns.example/filter"},
                       {"RemoteDNSDomain": "http://dns.google/dns-query"},
                       {"RemoteDNSDomain": "https://dns.google/dns-query#DIRECT"},
                       {"RemoteDNSIP": "not-an-ip"},
                       {"RemoteDNSDomain": "https://dns.google/dns-query#"},
                       {"RemoteDNSDomain": "https://dns.google:0/dns-query"}]:
            with self.subTest(change=change):
                cfg = {**self.cfg, **change}
                with self.assertRaises(ValueError):
                    build_mihomo.build(cfg, "test/routing", self.geosite, self.geoip)

    def test_dns_protocols_and_ipv6_bootstrap(self):
        examples = [
            ({"RemoteDNSType": "DoH", "RemoteDNSIP": "1.1.1.3", "RemoteDns": "8.8.8.8"},
             ("https://1.1.1.3/dns-query", ["1.1.1.3"])),
            ({"RemoteDNSType": "DoH", "RemoteDNSDomain": "https://dns.google/dns-query",
              "RemoteDns": "8.8.8.8"}, ("https://dns.google/dns-query", ["8.8.8.8"])),
            ({"RemoteDNSType": "DoT", "RemoteDNSDomain": "dns.example", "RemoteDNSIP": "1.1.1.3"},
             ("tls://dns.example", ["1.1.1.3"])),
            ({"RemoteDNSType": "DoQ", "RemoteDNSDomain": "quic://dns.example:853", "RemoteDNSIP": "1.1.1.3"},
             ("quic://dns.example:853", ["1.1.1.3"])),
            ({"RemoteDNSType": "DoU", "RemoteDNSIP": "2001:db8::1"},
             ("udp://[2001:db8::1]", ["2001:db8::1"])),
        ]
        for cfg, expected in examples:
            with self.subTest(cfg=cfg):
                self.assertEqual(build_mihomo.dns_resolver(cfg, "Remote"), expected)

    def test_legacy_doh_url_keeps_custom_family_path_with_bootstrap_ip(self):
        for kind in ("DoH", ""):
            cfg = {"RemoteDNSType": kind,
                   "RemoteDns": "https://doh.cleanbrowsing.org/doh/family-filter/",
                   "RemoteDNSIP": "185.228.168.168"}
            with self.subTest(kind=kind):
                self.assertEqual(build_mihomo.dns_resolver(cfg, "Remote"),
                                 (cfg["RemoteDns"], ["185.228.168.168"]))

    def test_dns_scheme_and_default_doh_path_are_normalized(self):
        cfg = {"RemoteDNSType": "DoH", "RemoteDNSDomain": "HTTPS://dns.google",
               "RemoteDNSIP": "8.8.8.8"}
        self.assertEqual(build_mihomo.dns_resolver(cfg, "Remote"),
                         ("https://dns.google/dns-query", ["8.8.8.8"]))

    def test_missing_one_dns_role_reuses_configured_resolver(self):
        for absent in ("Remote", "Domestic"):
            cfg = {k: v for k, v in self.cfg.items() if not k.startswith(absent)}
            profile, _ = build_mihomo.build(cfg, "test/routing", self.geosite, self.geoip)
            dns = profile["dns"]
            expected = ("https://common.dot.dns.yandex.net/dns-query" if absent == "Remote"
                        else "https://dns.google/dns-query")
            self.assertEqual(dns["nameserver"], [expected + "#PROXY"])
            self.assertEqual(dns["direct-nameserver"], [expected + "#DIRECT"])

    def test_invalid_template_is_rejected_before_generating_char_rules(self):
        for key, value in [("DirectSites", "example.com"), ("ProxyIp", [123]),
                           ("BlockSites", None), ("RouteOrder", False),
                           ("ProxySites", [" "]), ("DirectSites", ["domain:bad,PROXY"])]:
            with self.subTest(key=key, value=value):
                self.cfg[key] = value
                with self.assertRaises(ValueError):
                    self.build()
                self.cfg = copy.deepcopy(FIXTURE)

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
        with patch("sys.argv", ["build_mihomo.py", "--template", str(self.template),
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
        argv = ["build_mihomo.py", "--template", str(self.template), "--repo", "test/routing",
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
