#!/usr/bin/env python3
"""Собирает MIHOMO/default.yaml для поля маршрутизации Clash/Mihomo в 3x-ui.

Порядок и категории берутся из того же шаблона, что используется для Happ.
Списки экспортируют штатные сборщики v2fly: domain-list-community --exportlists
и geoip с output.type=text. Зависимость Python: PyYAML.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re

import yaml

ACTIONS = {"block": ("Block", "REJECT"), "proxy": ("Proxy", "PROXY"),
           "direct": ("Direct", "DIRECT")}
DOMAIN_TYPES = {"domain": "DOMAIN-SUFFIX", "full": "DOMAIN",
                "keyword": "DOMAIN-KEYWORD", "regexp": "DOMAIN-REGEX"}


def category_name(reference: str, kind: str) -> str:
    name = reference.removeprefix(kind + ":").lower()
    if not re.fullmatch(r"[a-z0-9!-]+", name):
        raise ValueError(f"Неподдерживаемая категория {reference!r}; нужна категория без @-фильтров")
    return name


def categories(cfg: dict, kind: str) -> list[str]:
    suffix = "Sites" if kind == "geosite" else "Ip"
    return sorted({category_name(ref, kind)
                   for prefix, _ in ACTIONS.values()
                   for ref in cfg.get(prefix + suffix, [])
                   if ref.startswith(kind + ":")})


def domain_rule(value: str) -> str:
    kind, separator, domain = value.partition(":")
    if not separator:
        kind, domain = "domain", value
    if kind not in DOMAIN_TYPES or not domain:
        raise ValueError(f"Неподдерживаемое доменное правило: {value!r}")
    return f"{DOMAIN_TYPES[kind]},{domain}"


def cidr(value: str) -> str:
    return str(ipaddress.ip_network(value, strict=False))


def geosite_payload(path: Path) -> str:
    rules = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # В штатном плоском экспорте DLC атрибуты имеют вид :@attr1,@attr2.
        # Категория уже разрешена сборщиком, фильтровать её повторно не нужно.
        rules.add(domain_rule(line.rsplit(":@", 1)[0]))
    if not rules:
        raise ValueError(f"Пустая категория GeoSite: {path}")
    return yaml.safe_dump({"payload": sorted(rules)}, allow_unicode=True, sort_keys=False)


def geoip_payload(path: Path) -> str:
    networks = {cidr(line.strip()) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")}
    if not networks:
        raise ValueError(f"Пустая категория GeoIP: {path}")
    return "\n".join(sorted(networks)) + "\n"


def build(cfg: dict, repo: str, geosite_dir: Path, geoip_dir: Path) -> tuple[dict, dict]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Укажите --repo USER/REPO (или GITHUB_REPOSITORY)")
    order = cfg.get("RouteOrder", "block-proxy-direct").split("-")
    if sorted(order) != sorted(ACTIONS):
        raise ValueError(f"Неподдерживаемый RouteOrder: {cfg.get('RouteOrder')!r}")

    providers, payloads, rules = {}, {}, []
    for action in order:
        prefix, target = ACTIONS[action]
        for suffix, kind in (("Sites", "geosite"), ("Ip", "geoip")):
            for value in cfg.get(prefix + suffix, []):
                if value.startswith(kind + ":"):
                    name = category_name(value, kind)
                    provider = f"{kind}-{name}"
                    extension = "yaml" if kind == "geosite" else "txt"
                    filename = f"rules/{provider}.{extension}"
                    if provider not in providers:
                        source = (geosite_dir if kind == "geosite" else geoip_dir) / f"{name}.txt"
                        payloads[filename] = (geosite_payload(source) if kind == "geosite"
                                              else geoip_payload(source))
                        providers[provider] = {
                            "type": "http",
                            "behavior": "classical" if kind == "geosite" else "ipcidr",
                            "format": extension.replace("txt", "text"),
                            "url": f"https://raw.githubusercontent.com/{repo}/main/MIHOMO/{filename}",
                            "path": f"./ruleset/personal-ru/{provider}.{extension}",
                            "interval": 86400,
                            "proxy": "PROXY",
                        }
                    rules.append(f"RULE-SET,{provider},{target}")
                elif kind == "geosite":
                    rules.append(f"{domain_rule(value)},{target}")
                else:
                    network = cidr(value)
                    rule_type = "IP-CIDR6" if ":" in network else "IP-CIDR"
                    rules.append(f"{rule_type},{network},{target}")
    global_proxy = str(cfg.get("GlobalProxy", "true")).lower()
    if global_proxy not in ("true", "false"):
        raise ValueError("GlobalProxy должен быть true или false")
    rules.append("MATCH," + ("PROXY" if global_proxy == "true" else "DIRECT"))
    profile = {
        "proxy-groups": [
            {"name": "PROXY", "type": "select", "proxies": ["AUTO"], "include-all": True},
            {"name": "AUTO", "type": "url-test", "include-all": True,
             "url": "https://www.gstatic.com/generate_204", "interval": 300,
             "tolerance": 100, "lazy": True},
        ],
        "rule-providers": providers,
        "rules": rules,
    }
    return profile, payloads


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", default="config/routing-template.json")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--geosite-dir", default="build/geosite")
    ap.add_argument("--geoip-dir", default="build/geoip")
    ap.add_argument("--outdir", default="MIHOMO")
    ap.add_argument("--list-geosite", action="store_true",
                    help="вывести категории для штатного --exportlists сборщика DLC")
    args = ap.parse_args()
    cfg = json.loads(Path(args.template).read_text(encoding="utf-8"))
    if args.list_geosite:
        print(",".join(categories(cfg, "geosite")))
        return 0
    profile, payloads = build(cfg, args.repo, Path(args.geosite_dir), Path(args.geoip_dir))
    # Сначала читаем и проверяем все списки; при ошибке файлы не перезаписываются.
    outdir = Path(args.outdir)
    for filename, contents in payloads.items():
        path = outdir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    # Удаляем списки, категории которых убраны из шаблона.
    rules_dir = outdir / "rules"
    if rules_dir.exists():
        for path in rules_dir.iterdir():
            if path.is_file() and f"rules/{path.name}" not in payloads:
                path.unlink()
    header = ("# Personal-RU: сгенерировано из config/routing-template.json.\n"
              "# Для 3x-ui → Подписка → Clash/Mihomo → Глобальные правила маршрутизации.\n"
              "# VPN-узлы добавляет панель. DNS/TUN настраиваются отдельно.\n")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "default.yaml").write_text(
        header + yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"OK: {outdir / 'default.yaml'}; списков: {len(payloads)}, правил: {len(profile['rules'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
