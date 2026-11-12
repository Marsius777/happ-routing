# Personal Happ / Mihomo Routing

Собственный профиль раздельной маршрутизации для [Happ](https://happ.su) и Mihomo (аналог RoscomVPN, но полностью под вашим контролем) + автосборка geosite.dat / geoip.dat.

Логика трафика (вы в России, VPS за границей, `GlobalProxy: true`):

| Категория | Куда | Что входит |
|---|---|---|
| **DIRECT** (мимо VPN) | напрямую | российские сервисы из [opencck iplist](https://russia.iplist.opencck.org) (Сбер, Госуслуги, налоговая, ВК, Яндекс — домены `geosite:russia-inside` + CIDR `geoip:russia-inside`), `category-ru`, `whitelist`, RU/BY подсети `geoip:direct`, Apple, Microsoft, Steam, Epic, Riot, Tarkov, Faceit, Twitch, Pinterest |
| **PROXY** (через VPN) | туннель | YouTube, Telegram, GitHub, Google Play, twitch-ads и весь остальной интернет |
| **BLOCK** | блок | реклама (`category-ads`), телеметрия Windows (`win-spy`) |

DNS: direct-трафик → Яндекс DoH (77.88.8.8), proxy-трафик → Google DoH (8.8.8.8). Для ЛК налоговой захардкожены IP (`DnsHosts`).

## Структура репозитория

```
config/routing-template.json   ← ШАБЛОН ПРОФИЛЯ — редактируйте списки здесь
custom/domains-extra.txt       ← свои домены в DIRECT (russia-inside)
custom/cidr-extra.txt          ← свои IP/CIDR в DIRECT (russia-inside)
scripts/fetch_opencck.py       ← качает списки opencck → домены + CIDR
scripts/build_routing.py       ← собирает ROUTING.JSON и happ://-ссылки
scripts/build_mihomo.py        ← собирает YAML и списки правил Mihomo из того же шаблона
.github/workflows/build.yml    ← автосборка (ежедневно + при каждом push)
release/                       ← geosite.dat, geoip.dat (генерируется CI)
HAPP/                          ← ROUTING.JSON, DEEPLINK, QR (генерируется CI)
MIHOMO/                        ← default.yaml и rules/* (генерируется CI)
```

Сборка geosite.dat: полный набор категорий [v2fly](https://github.com/v2fly/domain-list-community) + кастомные категории [roscomvpn-geosite](https://github.com/hydraponique/roscomvpn-geosite) (`whitelist`, `twitch-ads`, `faceit`…) + ваша `russia-inside`. Сборка geoip.dat: [roscomvpn-geoip](https://github.com/hydraponique/roscomvpn-geoip) (`geoip:direct`) + ваша `geoip:russia-inside`. Файлы публикуются в `release/` и раздаются через jsDelivr CDN с ветки `main` (работает из РФ; URL стабильный, CDN подтягивает свежие файлы в течение ~12 часов).

## Установка (один раз, ~10 минут)

1. Создайте на GitHub **публичный** репозиторий (jsDelivr не работает с приватными), например `happ-routing`.

2. Залейте файлы:
   ```bash
   cd happ-routing
   git init -b main
   git add .
   git commit -m "init"
   git remote add origin https://github.com/ВАШ_ЛОГИН/happ-routing.git
   git push -u origin main
   ```

3. Разрешите Actions писать в репозиторий: **Settings → Actions → General → Workflow permissions → Read and write permissions → Save**.

4. Запустите сборку: **Actions → Build geo-files and Happ/Mihomo routing → Run workflow** (push из шага 2 тоже мог её запустить). Ждите ~5 минут.

5. После сборки в репозитории появятся:
   - `HAPP/ROUTING.DEEPLINK` — ссылка `happ://routing/add/…` (ручное добавление);
   - `HAPP/ROUTING.ONADD.DEEPLINK` — ссылка `happ://routing/onadd/…` (для подписки: добавляет и сразу активирует);
   - `HAPP/ROUTING.QR.png` — QR-код для сканирования из Happ;
   - `release/geosite.dat`, `release/geoip.dat` + GitHub Release с тегом-датой.

## Подключение к панели 3x-ui

Чтобы роутинг прилетал клиентам автоматически вместе с подпиской (как в RoscomVPN):

1. Откройте `HAPP/ROUTING.ONADD.DEEPLINK` в вашем репозитории, скопируйте всю строку.
2. В панели 3x-ui: **Настройки панели → Подписка** → поле **«Правила маршрутизации Happ»** (в англ. интерфейсе — *Global routing rules for the VPN client (Happ only)*).
3. Вставьте ссылку, сохраните и перезапустите панель.
4. Клиент в Happ добавляет/обновляет подписку — панель отдаёт ссылку в HTTP-заголовке `routing`, Happ устанавливает профиль и качает ваши геофайлы.

Ручная установка без панели: открыть `ROUTING.DEEPLINK` на устройстве, отсканировать QR или вставить ссылку из буфера в Happ.

## Подключение Clash / Mihomo к 3x-ui

Та же сборка создаёт `MIHOMO/default.yaml` и `MIHOMO/rules/*` из
`config/routing-template.json`. Используются те же исходники, что для Happ:
плоский экспорт категорий v2fly + RoscomVPN + `russia-inside` и текстовый экспорт
GeoIP. В клиенте не нужны отдельные `.dat`: YAML ссылается на списки нашего репозитория.

После первого успешного workflow вставьте постоянную ссылку в
**3x-ui → Настройки → Подписка → Clash / Mihomo → Глобальные правила маршрутизации**:

```text
https://raw.githubusercontent.com/Marsius777/happ-routing/main/MIHOMO/default.yaml
```

Для другого форка замените `Marsius777/happ-routing` на свой репозиторий.
Сохраните настройки панели и обновите Clash/Mihomo-подписку в клиенте.

- Файл содержит `proxy-groups`, `rule-providers`, `rules` и, если DNS задан в шаблоне,
  секцию `dns`. Узлы VPN формирует 3x-ui; адресов подписок и ключей клиентов в публичном YAML нет.
- Группа `PROXY` позволяет выбрать узел вручную или `AUTO`. `AUTO` проверяет задержку
  раз в 300 секунд и выбирает узел по задержке, а не по загрузке CPU сервера.
- `RouteOrder` задаёт приоритет категорий. При `block-proxy-direct` блокировки идут
  первыми, затем явные VPN-правила, затем DIRECT; при `GlobalProxy: true`
  завершающее правило — `MATCH,PROXY`.
- Провайдеры списков обновляются самим Mihomo через `PROXY` раз в 86400 секунд. Новый порядок
  правил и изменения групп приходят при обновлении подписки через 3x-ui, после
  обновления удалённого YAML панелью.
- DNS переносится из Happ-шаблона. Для передачи секции `dns` из удалённого YAML
  включите в настройках Clash/Mihomo панели 3x-ui опцию **`subClashRemoteDNS`**
  (разрешение DNS из удалённого конфига). Без неё панель импортирует группы и правила,
  но отбрасывает DNS. Если переключателя в вашей версии нет, нужен поддерживающий его
  выпуск панели или настройка DNS непосредственно в клиенте.
- В Clash Verge отключите принудительную замену DNS из подписки глобальными настройками,
  если она включена. После обновления удалённого YAML панелью обновите подписку и переподключитесь.
- `DnsHosts`, FakeDNS и TUN из Happ-шаблона пока не переносятся. `Geositeurl`/`Geoipurl`
  тоже не используются: Mihomo получает категории через наши `rule-providers`.
- Экспорт GeoSite сохраняет суффиксы, точные домены, ключевые слова и регулярные
  выражения. Поддерживаются категории `geosite:name` / `geoip:name` без `@`-фильтров;
  неизвестные форматы и отсутствующие/пустые списки останавливают сборку.

DNS из общего шаблона:

| Поля Happ | Что получает Mihomo |
|---|---|
| `RemoteDNSType`, `RemoteDNSDomain` | Основной DNS и DNS доменных PROXY-исключений, через группу `PROXY` |
| `DomesticDNSType`, `DomesticDNSDomain` | DNS доменных DIRECT-исключений, DIRECT-соединений и имён VPN-серверов, через `DIRECT` |
| `DomesticDNSIP`, `RemoteDNSIP` | IP для первоначального разрешения имён DNS-серверов (`default-nameserver`) |
| `RemoteDns`, `DomesticDns` | Совместимость со старыми шаблонами: полный URL сохраняется, новый `*DNSDomain` имеет приоритет; старый голый IP заменяется новым `*DNSIP` |

Если в старом `*Dns` задан полный DoH/DoT URL, `*DNSIP` используется как bootstrap:
он не заменяет URL и не теряет путь семейного фильтра. Неизвестные протоколы,
нулевые/некорректные порты, фрагменты `#...` и неверные массивы правил останавливают
генерацию до записи файлов. Тесты используют отдельные фиксированные шаблоны,
поэтому изменение ваших DNS или `RouteOrder` не ломает их ожидаемые значения.

Поддерживаются `DoH`, `DoT`, `DoQ`, `DoU` (UDP) и TCP. Адреса DoH сохраняются вместе
с путём, например `https://doh.cleanbrowsing.org/doh/family-filter/`. Если задан
только один DNS, он используется в обеих ролях. Если DNS не задан вообще,
генератор оставляет его выбор клиенту. При `GlobalProxy: false` DNS по умолчанию
домашний; явно заданные PROXY-домены продолжают использовать удалённый DNS.

Доменные DNS-политики берутся из тех же категорий и явных доменов, что маршрутизация.
Для них создаются дополнительные `rules/dns-NN-proxy.yaml` и `rules/dns-NN-direct.yaml`.
Числовой префикс сохраняет приоритет `RouteOrder` после сортировки YAML-карт в 3x-ui.
Для IP-исключения, которое становится известно только после DNS-ответа, первичный
запрос идёт к DNS по умолчанию; домашний DNS используется при последующем DIRECT-разрешении.

Генератор не добавляет посторонний `fallback`. Для семейного профиля задайте
фильтрующий DNS **в обеих ролях**, чтобы домашний/удалённый резолвер не обходили
фильтрацию друг друга. Это настройка DNS текущей подписки, а не защита от всех
возможных обходов, например собственного DoH внутри стороннего приложения.

Локальная генерация после штатного экспорта списков в `build/geosite/*.txt` и
`build/geoip/*.txt`:

```bash
python3 -m pip install PyYAML
python3 scripts/build_mihomo.py --repo Marsius777/happ-routing
```

## Как редактировать

- **Перекинуть сервис между direct/proxy/block** — правьте массивы `DirectSites`, `ProxySites`, `BlockSites`, `DirectIp` и т.д. в `config/routing-template.json`. Доступны все категории v2fly (`geosite:netflix`, `geosite:openai`…), категории roscomvpn и ваша `geosite:russia-inside` / `geoip:russia-inside`.
- **Добавить свой домен/подсеть в direct** — допишите строку в `custom/domains-extra.txt` / `custom/cidr-extra.txt`.
- Сделайте `git push` — Actions пересоберёт всё автоматически и обновит `HAPP/`, `MIHOMO/` и `release/`.

## Обновления

- CI ежедневно тянет свежие списки opencck и базы v2fly/roscomvpn. Если что-то изменилось — новые `.dat` в `release/`, обновлённые файлы в `HAPP/`.
- URL геофайлов в ссылке стабильный (`@main`), поэтому ссылку в панели менять не обязательно: jsDelivr сам раздаёт свежие файлы (кэш до 12 часов).
- Чтобы заставить клиентов перекачать геофайлы немедленно: скопируйте свежий `ROUTING.ONADD.DEEPLINK` (новый `LastUpdated`) в поле подписки 3x-ui. Профиль называется одинаково (`Personal-RU`), новая ссылка перезаписывает старый.

## Примечания

- Списки opencck агрессивные: CIDR-зоны включают диапазоны CDN (Akamai и т.п.), которыми пользуются российские банки. Это нормально для сценария «я в России»: такой трафик просто пойдёт напрямую.
- Если у вас два сервера (РФ + зарубежный): профиль роутинга в Happ один на все подключения. С активным российским сервером конфликта нет — российский трафик всё равно идёт direct, остальное — через РФ-сервер (но YouTube и т.п. при этом не разблокируются, переключитесь на зарубежный).
- `Name` профиля и DNS-серверы меняются в `config/routing-template.json`.
