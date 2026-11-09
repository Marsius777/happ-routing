#!/usr/bin/env python3
"""
Генерирует из сервиса opencck iplist (https://russia.iplist.opencck.org):
  1. build/russia-inside        — список доменов в формате v2fly domain-list-community
  2. build/russia-inside.txt    — список CIDR (v4+v6) для сборки geoip.dat

Применяет секцию "replace" (узкие подсети вместо широких зон),
схлопывает пересекающиеся CIDR, убирает поддомены при наличии родителя.

Использование:
  python3 scripts/fetch_opencck.py                          # скачать с сервиса
  python3 scripts/fetch_opencck.py --source ip-list.json    # из локального файла
"""
import argparse
import gzip
import http.client
import ipaddress
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_SOURCE = "https://russia.iplist.opencck.org/?format=json"
DOWNLOAD_ATTEMPTS = 5
DOWNLOAD_TIMEOUT = 180
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def load(source: str) -> dict:
    if not source.startswith(("http://", "https://")):
        return json.loads(Path(source).read_text(encoding="utf-8"))

    req = urllib.request.Request(source, headers={
        "User-Agent": "happ-routing-build/1.0",
        "Accept-Encoding": "gzip",
    })
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as r:
                payload = r.read()
                if r.headers.get("Content-Encoding", "").strip().lower() == "gzip":
                    payload = gzip.decompress(payload)
            return json.loads(payload)
        except urllib.error.HTTPError as exc:
            if exc.code not in (408, 429) and not 500 <= exc.code < 600:
                raise
            error = exc
        except (OSError, http.client.HTTPException, EOFError, json.JSONDecodeError) as exc:
            error = exc

        if attempt == DOWNLOAD_ATTEMPTS:
            raise RuntimeError(
                f"Не удалось загрузить JSON из {source} "
                f"после {DOWNLOAD_ATTEMPTS} попыток"
            ) from error

        delay = 2 ** attempt
        print(
            f"Загрузка opencck: попытка {attempt}/{DOWNLOAD_ATTEMPTS} "
            f"не удалась ({type(error).__name__}: {error}). "
            f"Повтор через {delay} с.",
            file=sys.stderr,
        )
        time.sleep(delay)


def read_extra(path: str) -> list[str]:
    """Читает пользовательский файл: одна запись на строку, # — комментарий."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def collect_domains(site: dict) -> set[str]:
    domains = set(site.get("domains") or [])
    domains.update((site.get("external") or {}).get("domains") or [])
    return domains


def collect_cidrs(site: dict, fam: str) -> set[str]:
    cidrs = set(site.get(f"cidr{fam}") or [])
    # replace: широкая зона -> список узких подсетей
    repl = (site.get("replace") or {}).get(f"cidr{fam}") or {}
    for broad, narrow in repl.items():
        if broad in cidrs:
            cidrs.discard(broad)
            cidrs.update(narrow)
    ext = site.get("external") or {}
    cidrs.update(ext.get(f"cidr{fam}") or [])
    # одиночные IP -> host-маска (страховка, если cidr-зона их не покрывает)
    suffix = "/32" if fam == "4" else "/128"
    for ip in (site.get(f"ip{fam}") or []) + (ext.get(f"ip{fam}") or []):
        cidrs.add(f"{ip}{suffix}")
    return cidrs


def reduce_domains(domains: set[str]) -> list[str]:
    """Убирает поддомен, если в списке уже есть его родитель (suffix-match v2fly)."""
    cleaned = set()
    for d in domains:
        d = d.strip().lower().rstrip(".")
        if d.startswith("*."):
            d = d[2:]
        if DOMAIN_RE.match(d):
            cleaned.add(d)
    kept: set[str] = set()
    for d in sorted(cleaned, key=lambda x: x.count(".")):
        parts = d.split(".")
        # есть ли среди уже принятых какой-либо родительский суффикс (минимум 2 метки)
        if any(".".join(parts[i:]) in kept for i in range(1, len(parts) - 1)):
            continue
        kept.add(d)
    return sorted(kept)


def collapse(cidrs: set[str], version: int) -> list[str]:
    nets = []
    for c in cidrs:
        try:
            n = ipaddress.ip_network(c, strict=False)
        except ValueError:
            print(f"  ! пропущен некорректный CIDR: {c}", file=sys.stderr)
            continue
        if n.version == version:
            nets.append(n)
    return [str(n) for n in ipaddress.collapse_addresses(nets)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=DEFAULT_SOURCE, help="URL или путь к JSON opencck")
    ap.add_argument("--outdir", default="build")
    ap.add_argument("--extra-domains", default="custom/domains-extra.txt")
    ap.add_argument("--extra-cidr", default="custom/cidr-extra.txt")
    args = ap.parse_args()

    data = load(args.source)
    print(f"Источник: {args.source} — {len(data)} сервисов")

    domains: set[str] = set(read_extra(args.extra_domains))
    cidrs: set[str] = set(read_extra(args.extra_cidr))
    for site in data.values():
        domains |= collect_domains(site)
        cidrs |= collect_cidrs(site, "4")
        cidrs |= collect_cidrs(site, "6")

    domain_list = reduce_domains(domains)
    cidr4 = collapse(cidrs, 4)
    cidr6 = collapse(cidrs, 6)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Файл категории geosite:russia-inside (имя файла = имя категории)
    (outdir / "russia-inside").write_text(
        "\n".join(domain_list) + "\n", encoding="utf-8"
    )
    # CIDR для geoip-сборщика (text input понимает v4 и v6 вперемешку)
    (outdir / "russia-inside.txt").write_text(
        "\n".join(cidr4 + cidr6) + "\n", encoding="utf-8"
    )

    print(f"Доменов: {len(domain_list)} -> {outdir/'russia-inside'}")
    print(f"CIDR: v4={len(cidr4)} v6={len(cidr6)} -> {outdir/'russia-inside.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
