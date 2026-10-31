#!/usr/bin/env python3
"""
Собирает итоговый Happ-профиль маршрутизации из шаблона:
  HAPP/ROUTING.JSON           — итоговый JSON (для просмотра/редактирования)
  HAPP/ROUTING.DEEPLINK       — happ://routing/add/{base64}   (ручное добавление)
  HAPP/ROUTING.ONADD.DEEPLINK — happ://routing/onadd/{base64} (для подписки 3x-ui:
                                добавляет И активирует профиль автоматически)

Подставляет в шаблон ссылки на geosite/geoip вашего репозитория (jsdelivr,
пин по тегу) и метку LastUpdated — она заставляет Happ перекачать геофайлы.

Использование (локально):
  python3 scripts/build_routing.py --repo USER/REPO --tag 202606111200
В GitHub Actions --repo берётся из $GITHUB_REPOSITORY автоматически.
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="config/routing-template.json")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"),
                    help="владелец/репозиторий на GitHub, напр. user/happ-routing")
    ap.add_argument("--tag", required=True, help="тег релиза, напр. 202606111200")
    ap.add_argument("--last-updated", default=str(int(time.time())))
    ap.add_argument("--outdir", default="HAPP")
    args = ap.parse_args()

    if not args.repo or "/" not in args.repo:
        print("Ошибка: укажите --repo USER/REPO (или запускайте в GitHub Actions)",
              file=sys.stderr)
        return 1

    cfg = json.loads(Path(args.template).read_text(encoding="utf-8"))
    # @main вместо @тега: ссылка стабильна (не меняется при сборках),
    # Стабильные ссылки на геобазы из ветки main через GitHub Raw
    base = f"https://raw.githubusercontent.com/{args.repo}/main/release"
    cfg["Geositeurl"] = f"{base}/geosite.dat"
    cfg["Geoipurl"] = f"{base}/geoip.dat"
    cfg["LastUpdated"] = args.last_updated

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    (outdir / "ROUTING.JSON").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    b64 = base64.b64encode(
        json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    (outdir / "ROUTING.DEEPLINK").write_text(
        f"happ://routing/add/{b64}\n", encoding="utf-8")
    (outdir / "ROUTING.ONADD.DEEPLINK").write_text(
        f"happ://routing/onadd/{b64}\n", encoding="utf-8")

    print(f"OK: профиль '{cfg.get('Name')}', geo-тег {args.tag}")
    print(f"  {outdir}/ROUTING.JSON")
    print(f"  {outdir}/ROUTING.DEEPLINK")
    print(f"  {outdir}/ROUTING.ONADD.DEEPLINK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
