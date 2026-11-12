#!/usr/bin/env python3
"""Собирает MIHOMO/default.yaml для поля маршрутизации Clash/Mihomo в 3x-ui.

Порядок, категории и DNS берутся из того же шаблона, что используется для Happ.
Списки экспортируют штатные сборщики v2fly: domain-list-community --exportlists
и geoip с output.type=text. Зависимость Python: PyYAML.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

ACTIONS = {"block": ("Block", "REJECT"), "proxy": ("Proxy", "PROXY"),
           "direct": ("Direct", "DIRECT")}
DOMAIN_TYPES = {"domain": "DOMAIN-SUFFIX", "full": "DOMAIN",
                "keyword": "DOMAIN-KEYWORD", "regexp": "DOMAIN-REGEX"}


def validate_template(cfg: dict) -> None:
    if not isinstance(cfg, dict):
        raise ValueError("Шаблон маршрутизации должен быть JSON-объектом")
    for prefix, _ in ACTIONS.values():
        for suffix in ("Sites", "Ip"):
            key = prefix + suffix
            values = cfg.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f"{key} должен быть массивом строк")
            for index, value in enumerate(values):
                if (not isinstance(value, str) or not value.strip()
                        or value != value.strip() or any(c in value for c in "\r\n\0")):
                    raise ValueError(f"{key}[{index}] должен быть непустой строкой без лишних пробелов")
    order = cfg.get("RouteOrder", "block-proxy-direct")
    if not isinstance(order, str) or sorted(order.split("-")) != sorted(ACTIONS):
        raise ValueError(f"Неподдерживаемый RouteOrder: {order!r}")


def category_name(reference: str, kind: str) -> str:
    name = reference.removeprefix(kind + ":").lower()
    if not re.fullmatch(r"[a-z0-9!-]+", name):
        raise ValueError(f"Неподдерживаемая категория {reference!r}; нужна категория без @-фильтров")
    return name


def categories(cfg: dict, kind: str) -> list[str]:
    validate_template(cfg)
    suffix = "Sites" if kind == "geosite" else "Ip"
    return sorted({category_name(ref, kind)
                   for prefix, _ in ACTIONS.values()
                   for ref in cfg.get(prefix + suffix, [])
                   if ref.startswith(kind + ":")})


def domain_rule(value: str) -> str:
    kind, separator, domain = value.partition(":")
    if not separator:
        kind, domain = "domain", value
    if (kind not in DOMAIN_TYPES or not domain or any(c in domain for c in "\r\n\0")
            or (kind != "regexp" and "," in domain)):
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


def rule_provider(repo: str, filename: str, behavior: str) -> dict:
    return {
        "type": "http", "behavior": behavior,
        "format": "text" if filename.endswith(".txt") else "yaml",
        "url": f"https://raw.githubusercontent.com/{repo}/main/MIHOMO/{filename}",
        "path": f"./ruleset/personal-ru/{Path(filename).name}",
        "interval": 86400, "proxy": "PROXY",
    }


def dns_resolver(cfg: dict, prefix: str) -> tuple[str, list[str]]:
    """Happ DNS fields -> Mihomo resolver URL and explicit bootstrap IPs."""
    def field(suffix: str) -> str:
        value = cfg.get(prefix + suffix, "")
        if not isinstance(value, str):
            raise ValueError(f"{prefix + suffix} должен быть строкой")
        return value.strip()

    legacy, address, domain, kind = (field(s) for s in ("Dns", "DNSIP", "DNSDomain", "DNSType"))
    if not any((legacy, address, domain, kind)):
        return "", []
    bootstrap = []
    if address:
        try:
            bootstrap.append(str(ipaddress.ip_address(address)))
        except ValueError as exc:
            raise ValueError(f"{prefix}DNSIP должен содержать IP-адрес") from exc
    elif legacy:
        try:
            bootstrap.append(str(ipaddress.ip_address(legacy)))
        except ValueError:
            pass
    if not kind:
        endpoint = domain or legacy or address
        if "://" in endpoint:
            scheme = urlsplit(endpoint).scheme.lower()
            inferred = {"https": "doh", "tls": "dot", "quic": "doq", "tcp": "tcp", "udp": "dou"}
            if scheme not in inferred:
                raise ValueError(f"Неподдерживаемый протокол {prefix} DNS: {scheme!r}")
            kind = inferred[scheme]
        else:
            kind = "dou"
    kind = kind.lower()
    schemes = {"doh": "https", "dot": "tls", "doq": "quic", "dou": "udp",
               "udp": "udp", "tcp": "tcp"}
    if kind not in schemes:
        raise ValueError(f"Неподдерживаемый {prefix}DNSType: {kind!r}")
    scheme = schemes[kind]
    # В старых шаблонах Dns может быть полным URL с важным путём (Family и т.п.).
    # DNSIP в таком случае — bootstrap, а не замена URL. Для старого голого IP
    # новое поле DNSIP, напротив, имеет приоритет.
    legacy_endpoint = legacy
    try:
        ipaddress.ip_address(legacy)
        legacy_endpoint = ""
    except ValueError:
        pass
    endpoint = ((address or legacy or domain) if scheme in ("udp", "tcp")
                else (domain or legacy_endpoint or address or legacy))
    if not endpoint:
        raise ValueError(f"Не задан адрес {prefix} DNS")
    if "://" not in endpoint:
        try:
            ip = ipaddress.ip_address(endpoint)
            endpoint = f"[{ip}]" if ip.version == 6 else str(ip)
        except ValueError:
            pass
        endpoint = f"{scheme}://{endpoint}"
        if scheme == "https" and not urlsplit(endpoint).path:
            endpoint += "/dns-query"
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        if (parsed.scheme != scheme or not parsed.hostname or "#" in endpoint
                or (port is not None and not 1 <= port <= 65535)
                or parsed.username or parsed.password or any(c.isspace() for c in endpoint)
                or (scheme != "https" and (parsed.path or parsed.query))):
            raise ValueError()
    except ValueError as exc:
        raise ValueError(f"Некорректный адрес {prefix} DNS для {kind}: {endpoint!r}") from exc
    if scheme == "https" and not parsed.path:
        parsed = parsed._replace(path="/dns-query")
    endpoint = parsed._replace(scheme=scheme).geturl()
    if not bootstrap:
        try:
            bootstrap.append(str(ipaddress.ip_address(parsed.hostname)))
        except ValueError:
            pass
    return endpoint, bootstrap


def build_dns(cfg: dict, repo: str, order: list[str], global_proxy: bool,
              domain_lists: dict, providers: dict, payloads: dict) -> dict | None:
    remote, remote_ips = dns_resolver(cfg, "Remote")
    domestic, domestic_ips = dns_resolver(cfg, "Domestic")
    if not remote and not domestic:
        return None
    remote, domestic = remote or domestic, domestic or remote
    bootstrap = list(dict.fromkeys(domestic_ips + remote_ips))
    if not bootstrap:
        raise ValueError("Для DNS bootstrap задайте DomesticDNSIP или RemoteDNSIP")
    resolvers = {"proxy": remote + "#PROXY", "direct": domestic + "#DIRECT"}
    policy = {}
    for index, action in enumerate(order):
        if action == "block" or not domain_lists[action]:
            continue
        # 3x-ui разбирает YAML в Go map и может пересортировать ключи политики.
        # Числовые имена сохраняют RouteOrder даже после такого преобразования.
        name = f"dns-{index:02d}-{action}"
        filename = f"rules/{name}.yaml"
        payloads[filename] = yaml.safe_dump(
            {"payload": sorted(domain_lists[action])}, allow_unicode=True, sort_keys=False)
        providers[name] = rule_provider(repo, filename, "classical")
        policy[f"rule-set:{name}"] = [resolvers[action]]
    return {
        "enable": True,
        "default-nameserver": bootstrap,
        "proxy-server-nameserver": [resolvers["direct"]],
        "nameserver": [resolvers["proxy" if global_proxy else "direct"]],
        "direct-nameserver": [resolvers["direct"]],
        "nameserver-policy": policy,
    }


def build(cfg: dict, repo: str, geosite_dir: Path, geoip_dir: Path) -> tuple[dict, dict]:
    validate_template(cfg)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Укажите --repo USER/REPO (или GITHUB_REPOSITORY)")
    order = cfg.get("RouteOrder", "block-proxy-direct").split("-")
    if sorted(order) != sorted(ACTIONS):
        raise ValueError(f"Неподдерживаемый RouteOrder: {cfg.get('RouteOrder')!r}")

    providers, payloads, rules = {}, {}, []
    domain_lists = {action: set() for action in ACTIONS}
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
                        providers[provider] = rule_provider(
                            repo, filename, "classical" if kind == "geosite" else "ipcidr")
                    if kind == "geosite":
                        domain_lists[action].update(yaml.safe_load(payloads[filename])["payload"])
                    rules.append(f"RULE-SET,{provider},{target}")
                elif kind == "geosite":
                    domain_lists[action].add(domain_rule(value))
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
    dns = build_dns(cfg, repo, order, global_proxy == "true", domain_lists, providers, payloads)
    if dns is not None:
        profile["dns"] = dns
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
              "# VPN-узлы добавляет панель; для передачи DNS включите subClashRemoteDNS.\n")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "default.yaml").write_text(
        header + yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"OK: {outdir / 'default.yaml'}; списков: {len(payloads)}, правил: {len(profile['rules'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
