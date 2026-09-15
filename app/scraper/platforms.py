"""Интеграция с электронными площадками, указанными в сообщениях ЕФРСБ.

Стратегия: на странице ЕФРСБ в тексте сообщения указаны площадка и (часто)
номер лота. На сайте самой площадки публикуется карточка лота с фотографиями
и документами — их мы и извлекаем. QRATOR на площадках обычно отсутствует.

Пути поиска карточки лота на площадке:
  1. если в сообщении был прямой URL (`platform_lot_url`) — идём сразу туда;
  2. поиск по `site:<домен>` (веб-поиск Bing) по VIN / марке / названию лота.
"""
from __future__ import annotations

import html as html_mod
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass

import requests

log = logging.getLogger("efrsb.platforms")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# подстрока названия площадки -> основной домен
PLATFORM_DOMAINS: dict[str, str] = {
    "utender": "utender.ru",
    "sberbank-ast": "utp.sberbank-ast.ru",
    "сбербанк-аст": "utp.sberbank-ast.ru",
    "сбербанк аст": "utp.sberbank-ast.ru",
    "фабрикант": "www.fabrikant.ru",
    "rts-tender": "www.rts-tender.ru",
    "ртс-тендер": "www.rts-tender.ru",
    "ртс тендер": "www.rts-tender.ru",
    "lot-online": "www.lot-online.ru",
    "лот-онлайн": "www.lot-online.ru",
    "центр дистанционных торгов": "cdtr.ru",
    "roseltorg": "www.roseltorg.ru",
    "росэлторг": "www.roseltorg.ru",
    "аукционный дом": "auction-house.ru",
    "арестованное имущество": "arestim.ru",
    "арестованноеимущество": "arestim.ru",
    "вэтп": "www.etprf.ru",
    "вевэтп": "www.etprf.ru",
    "волжская этп": "www.etprf.ru",
    "этп авто": "etp-auto.ru",
    "автолот": "autolot.ru",
}

_IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp|avif|bmp)(?:\?|$)", re.I)


@dataclass
class PlatformLot:
    page_url: str
    html: str


def resolve_platform(raw: str | None) -> str | None:
    """По названию/URL площадки из сообщения вернуть канонический домен."""
    if not raw:
        return None
    low = raw.lower()
    for chunk, domain in PLATFORM_DOMAINS.items():
        if chunk in low:
            return domain
    m = re.search(r"https?://([^/\s]+)", raw)
    if m:
        return urllib.parse.urlparse(m.group(1)).netloc
    return None


def extract_platform_info(text: str) -> tuple[str, str]:
    """Из текста сообщения: (канонический домен площадки, прямой URL лота)."""
    domain = ""
    lot_url = ""
    # первый HTTP-URL в тексте, похожий на площадку/лот
    urls = re.findall(r"https?://[^\s<>()\"']+", text)
    for u in urls:
        host = urllib.parse.urlparse(u).netloc
        for chunk, dom in PLATFORM_DOMAINS.items():
            if chunk in host:
                host = dom
        if any(chunk in u.lower() for chunk in PLATFORM_DOMAINS):
            domain = domain or host
            if not lot_url and not _is_root_url(u):
                lot_url = u
            break
    # метка «Электронная площадка: ...»
    for m in re.finditer(
        r"(?im)^[ \t]*(?:электронная (?:торговая )?площадка|оператор электронной площадки"
        r"|этп|площадка проведения торгов)\s*[:)|]\s*(.{5,200})",
        text,
    ):
        v = re.sub(r"\s+", " ", m.group(1)).strip()
        v = v.rstrip(".,;")
        if not domain:
            domain = resolve_platform(v) or ""
        # внутри значения может быть URL лота
        u = re.search(r"https?://[^\s<>()\"']+", v)
        if u and not lot_url and not _is_root_url(u.group(0)):
            lot_url = u.group(0)
        if domain and lot_url:
            break
    # ссылка в тексте лота: «Номер лота на площадке ... ссылка»
    for m in re.finditer(r"(?im)(?:сайт[ \t]+площадки|площадка|оператор)\s*[:)|]\s*(https?://[^\s<>()\"']+)", text):
        u = m.group(1)
        if not lot_url and not _is_root_url(u):
            lot_url = u
        if not domain:
            domain = resolve_platform(u) or ""
    return domain, lot_url


# ---------------------------------------------------------------------------
# Поиск карточки лота на площадке
# ---------------------------------------------------------------------------
def _sess() -> requests.Session:
    from app.config import settings

    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    s.trust_env = False
    if settings.proxies:
        s.proxies.update(settings.proxies)
    return s


def _web_search(query: str, num: int = 20) -> list[str]:
    """Веб-поиск -> список URL. DDG (html) даёт прямые ссылки; Bing — фолбэк."""
    import time

    q = urllib.parse.quote(query)
    time.sleep(_DDG_DELAY_S)

    def from_ddg() -> list[str]:
        try:
            r = _sess().get(
                f"https://html.duckduckgo.com/html/?q={q}&kl=ru-ru",
                headers={"Referer": "https://duckduckgo.com/"},
                timeout=25,
            )
            low = r.text.lower()
            if r.status_code != 200 or "anomaly" in low or "captcha" in low:
                log.debug("ddg html: %s (rate limited?)", r.status_code)
                return []
            out: list[str] = []
            for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"', r.text):
                u = html_mod.unescape(m.group(1))
                if "uddg=" in u:
                    u = urllib.parse.unquote(urllib.parse.unquote(u.split("uddg=", 1)[1].split("&", 1)[0]))
                if u.startswith("http"):
                    out.append(u)
            return out
        except Exception as e:  # noqa: BLE001
            log.debug("ddg html search error: %s", e)
            return []

    def from_bing() -> list[str]:
        try:
            r = _sess().get(
                f"https://www.bing.com/search?q={q}&count={num}&setlang=ru",
                headers={"Referer": "https://www.bing.com/"},
                timeout=25,
            )
            if r.status_code != 200:
                return []
            body = html_mod.unescape(r.text)
            out: list[str] = []
            for blk in re.finditer(r'<li class="b_algo".*?</li>', body, re.S):
                m = re.search(r"<h2[^>]*>\s*<a[^>]+href=\"[^\"]*u=a1([A-Za-z0-9_-]+)", blk.group(0))
                if not m:
                    continue
                try:
                    padded = m.group(1) + "=" * (-len(m.group(1)) % 4)
                    u = _b64_decode(padded)
                except Exception:  # noqa: BLE001
                    continue
                if u.startswith("http"):
                    out.append(u)
            return out
        except Exception as e:  # noqa: BLE001
            log.debug("bing search error: %s", e)
            return []

    results = from_ddg()
    if not results:
        results = from_bing()
    out: list[str] = []
    for u in results:
        if u not in out:
            out.append(u)
    return out[:num]


_DDG_DELAY_S = 3.0


def _b64_decode(s: str) -> str:
    import base64

    return base64.urlsafe_b64decode(s.encode("ascii")).decode("utf-8", "replace")


_LOT_PAGE_RE = re.compile(
    r"/(?:lots?|view|auction|torgi|trade)(?:[/_-]|$)",
    re.I,
)


def find_platform_lot(
    domain: str,
    lot: dict,
    direct_url: str = "",
) -> PlatformLot | None:
    """Найти карточку лота на площадке. Возвращает (url, html) или None."""
    targets = [direct_url] if direct_url and not _is_root_url(direct_url) else []
    if not targets:
        queries: list[str] = []
        v = (lot.get("vin") or "").strip()
        mm = (lot.get("make_model") or "").strip()
        tt = (lot.get("title") or "").strip()
        lot_no = (lot.get("lot_no") or "").strip()
        if v:
            queries.append(f"site:{domain} {v}")
            if mm:
                queries.append(f"site:{domain} {mm} {v}")
        if tt:
            words = " ".join(tt.split()[:6])
            queries.append(f"site:{domain} {words}")
        if v and lot_no:
            queries.append(f"site:{domain} лот {lot_no} {v}")
        seen: set[str] = set()
        for query in queries:
            for u in _web_search(query):
                if u in seen:
                    continue
                seen.add(u)
                targets.append(u)
                if len(targets) >= 3:
                    break
            if targets:
                break
    if not targets:
        return None

    for u in targets:
        if not u:
            continue
        try:
            r = _sess().get(
                u,
                headers={"Referer": f"https://{domain}/", "Accept-Language": "ru-RU,ru;q=0.9"},
                timeout=(10, 30),
            )
            if r.status_code != 200:
                continue
            # разворачиваем редиректы (напр. bing /ck/a) и проверяем хост
            final = r.url or u
            if not _same_platform(final, domain):
                continue
            html = r.text
            looks_lot = bool(_LOT_PAGE_RE.search(final))
            has_media = len(extract_images(html, final)) > 0 or "фото" in html.lower()[:20000]
            if looks_lot or has_media:
                log.info("площадка %s: карточка лота %s", domain, final)
                return PlatformLot(page_url=final, html=html)
        except Exception as e:  # noqa: BLE001
            log.debug("площадка %s: %s (%s)", domain, e, u)
    return None


def _same_platform(url: str, domain: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    d = domain.lower()
    return host == d or host.endswith("." + d) or d.endswith("." + host) or host in d or d in host


def _is_root_url(url: str) -> bool:
    """Главная страница площадки — не карточка лота."""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:  # noqa: BLE001
        return True
    path = p.path.rstrip("/")
    return path in ("", "/") and not p.query


# ---------------------------------------------------------------------------
# Извлечение изображений со страницы лота
# ---------------------------------------------------------------------------
def extract_images(html: str, page_url: str, limit: int = 40) -> list[str]:
    """Все изображения со страницы лота (og:image, img src/data-src, ссылки на файлы)."""
    base = urllib.parse.urlparse(page_url)
    base_url = f"{base.scheme}://{base.netloc}"
    found: list[str] = []

    def add(u: str | None) -> None:
        if not u or not u.strip():
            return
        u = u.strip()
        if u.startswith("data:"):
            return
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = base_url + u
        elif not u.startswith("http"):
            u = urllib.parse.urljoin(page_url, u)
        u = html_mod.unescape(u).split("#")[0]
        low = u.lower()
        if any(bad in low for bad in (".css", ".js?", "/favicon", ".svg", ".ico", ".axd")):
            return
        if low.split("?")[0].endswith((".css", ".js", ".svg", ".ico")):
            return
        if u in found:
            return
        found.append(u)

    for m in re.finditer(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html):
        add(m.group(1))
    for m in re.finditer(r'<img[^>]+(?:src|data-src|data-full)="([^"]+)"', html):
        add(m.group(1))
    for m in re.finditer(r'<a[^>]+href="([^"]+\.(?:jp[e]?g|png|webp|avif|bmp))"', html, re.I):
        add(m.group(1))
    # галереи: prettyPhoto / fancybox / lightbox — картинки по ссылкам
    for m in re.finditer(r"<a[^>]+rel=[\"']?[^\"']*prettyphoto[^\"']*[\"']?[^>]+href=\"([^\"]+)\"", html, re.I):
        add(m.group(1))
    for m in re.finditer(r"<a[^>]+data-(?:fancybox|lightbox|gallery|photo)[^>]+href=\"([^\"]+)\"", html, re.I):
        add(m.group(1))
    # зачистка от микро-картинок-декораций
    out = []
    for u in found:
        low = u.lower()
        if any(x in low for x in ("icon", "logo", "sprite", "spacer", "pixel")):
            continue
        out.append(u)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------
def find_platform_photos(lot: dict, domain: str, direct_url: str = "") -> tuple[str, list[str]]:
    """Полный цикл: найти карточку лота на площадке и собрать URL фото."""
    pl = find_platform_lot(domain, lot, direct_url)
    if not pl:
        return "", []
    return pl.page_url, extract_images(pl.html, pl.page_url)


# ---------------------------------------------------------------------------
# Прямой парсинг каталогов площадок
# ---------------------------------------------------------------------------
def scrape_lot_online(categories: list[str] | None = None, page: int = 1) -> list[dict]:
    """Парсинг каталога lot-online.ru (РАД). Возвращает список лотов."""
    # Категории транспорта на lot-online.ru
    TRANSPORT_CATEGORIES = {
        "44": "Легковые автомобили",
        "45": "Грузовой транспорт, автобусы, спецтехника",
        "46": "Бронеавтомобили",
        "47": "Автобусы, микроавтобусы",
        "48": "Прицепы",
        "49": "Водный транспорт",
        "50": "Воздушный транспорт",
        "51": "Ж.-д. транспорт и подвижной состав",
        "52": "Специализированная техника",
        "53": "Иной транспорт",
        "165": "Мототехника",
        "172": "Несколько транспортных средств",
        "176": "Годные остатки транспорта",
    }
    cat_ids = categories or list(TRANSPORT_CATEGORIES.keys())
    results = []
    sess = _sess()
    for cid in cat_ids:
        url = f"https://catalog.lot-online.ru/index.php?dispatch=categories.view&category_id={cid}&page={page}"
        try:
            r = sess.get(url, timeout=30)
            if r.status_code != 200:
                log.warning("lot-online cat %s: HTTP %s", cid, r.status_code)
                continue
            product_ids = list(set(re.findall(r"product_id=(\d+)", r.text)))
            for pid in product_ids:
                lot_data = _parse_lot_online_product(sess, pid)
                if lot_data:
                    lot_data["trade_platform"] = "lot-online.ru"
                    lot_data["category"] = TRANSPORT_CATEGORIES.get(cid, cid)
                    results.append(lot_data)
            time.sleep(1)
        except Exception as e:
            log.warning("lot-online cat %s: %s", cid, e)
    return results


def _parse_lot_online_product(sess: requests.Session, pid: str) -> dict | None:
    """Парсинг страницы товара на lot-online.ru."""
    url = f"https://catalog.lot-online.ru/index.php?dispatch=products.view&product_id={pid}"
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200:
            return None
        text = r.text
        title_m = re.search(r"<title>([^<]+)</title>", text)
        title = html_mod.unescape(title_m.group(1).strip()) if title_m else ""
        if not title:
            return None

        vin = ""
        vin_m = re.search(r"VIN[:\s]+([A-Z0-9]{17})", title)
        if vin_m:
            vin = vin_m.group(1).upper()

        make_model = ""
        mm_m = re.search(r"марка и модель\s*[-—]\s*([^.]+)", title, re.I)
        if not mm_m:
            # Формат: "АМТС MERCEDES-BENZ GLE 350 D 4MATIC, 2017 г.в., VIN ..."
            # Извлекаем всё от первого слова до года
            mm_m = re.match(r"^[А-Яа-я\s]*([A-Z][A-Z0-9\s\-]+),\s*\d{4}", title)
            if mm_m:
                make_model = mm_m.group(1).strip()
                make_model = re.sub(r"^АМТС\s+", "", make_model, flags=re.I).strip()
        if mm_m and not make_model:
            make_model = mm_m.group(1).strip() if mm_m.lastindex else mm_m.group(0).strip()
            make_model = re.sub(r"^АМТС\s+", "", make_model, flags=re.I).strip()
            make_model = re.sub(r",?\s*VIN\s+[A-Z0-9]{17}\s*$", "", make_model, flags=re.I).strip()
            make_model = re.sub(r",?\s*\d{4}\s*г\.в\.?\s*$", "", make_model, flags=re.I).strip()

        year = ""
        year_m = re.search(r"год выпуска\s*[-—]\s*(\d{4})", title, re.I)
        if not year_m:
            year_m = re.search(r"(\d{4})\s*г\.в\.", title, re.I)
        if year_m:
            year = year_m.group(1)

        price = None
        price_m = re.search(r"([\d\s]+)\s*(?:руб|₽)", title)
        if price_m:
            try:
                price = float(price_m.group(1).replace(" ", "").replace("\xa0", ""))
            except ValueError:
                pass

        # Цена из блока на странице
        if price is None:
            price_m2 = re.findall(r"([\d\s]+)\s*(?:руб|₽)", text)
            for pm in price_m2:
                try:
                    val = float(pm.replace(" ", "").replace("\xa0", ""))
                    if val > 1000:
                        price = val
                        break
                except ValueError:
                    pass

        imgs = []
        for m in re.finditer(r'cdn/[^"\']+\.(?:jpg|jpeg|png|webp)', text):
            img = m.group(0)
            if img not in imgs:
                imgs.append(img)

        return {
            "efrsb_id": f"lot-online:{pid}",
            "lot_no": pid,
            "title": title,
            "vin": vin or None,
            "make_model": (f"{make_model} {year}".strip() if year else make_model) or None,
            "price_amount": price,
            "price_currency": "RUB",
            "source_url": url,
            "platform_lot_url": url,
            "raw": {"images": imgs[:40], "platform": "lot-online.ru", "product_id": pid},
        }
    except Exception as e:
        log.debug("lot-online product %s: %s", pid, e)
        return None


def scrape_auction_house(page: int = 1) -> list[dict]:
    """Парсинг каталога auction-house.ru. Возвращает список лотов транспорта."""
    CATEGORIES = [
        "/catalog/dvizhimoe/transport/legkovye-avto/",
        "/catalog/dvizhimoe/transport/gruzoviki-avtobusy/",
        "/catalog/dvizhimoe/transport/moto/",
    ]
    results = []
    sess = _sess()
    for cat_path in CATEGORIES:
        url = f"https://auction-house.ru{cat_path}"
        try:
            r = sess.get(url, timeout=30)
            if r.status_code != 200:
                log.warning("auction-house %s: HTTP %s", cat_path, r.status_code)
                continue
            text = r.text
            # Извлекаем ссылки на лоты
            lot_links = list(set(re.findall(
                r'href="(' + re.escape(cat_path) + r'[^/]+/)"', text
            )))
            for link in lot_links:
                lot_data = _parse_auction_house_lot(sess, link)
                if lot_data:
                    results.append(lot_data)
            # Пагинация
            pages_found = [int(p) for p in re.findall(r'PAGEN_2=(\d+)', text)]
            max_page = max(pages_found) if pages_found else 1
            if page < max_page:
                next_url = f"{url}?PAGEN_2={page + 1}"
                try:
                    r2 = sess.get(next_url, timeout=30)
                    if r2.status_code == 200:
                        more_links = list(set(re.findall(
                            r'href="(' + re.escape(cat_path) + r'[^/]+/)"', r2.text
                        )))
                        for link in more_links:
                            if link not in lot_links:
                                lot_data = _parse_auction_house_lot(sess, link)
                                if lot_data:
                                    results.append(lot_data)
                except Exception:
                    pass
            time.sleep(1)
        except Exception as e:
            log.warning("auction-house %s: %s", cat_path, e)
    return results


def _parse_auction_house_lot(sess: requests.Session, path: str) -> dict | None:
    """Парсинг страницы лота на auction-house.ru."""
    url = f"https://auction-house.ru{path}"
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200:
            return None
        text = r.text
        title_m = re.search(r"<title>([^<]+)</title>", text)
        title = html_mod.unescape(title_m.group(1).strip()) if title_m else ""
        if not title:
            return None

        vin = ""
        vin_m = re.search(r"VIN[:\s]+([A-Z0-9]{17})", title, re.I)
        if vin_m:
            vin = vin_m.group(1).upper()

        make_model = ""
        mm_m = re.search(r"(?:Автомобиль марки|Транспортное средство)\s+([^.]+?)(?:,|\s+\d{4})", title, re.I)
        if mm_m:
            make_model = mm_m.group(1).strip()

        year = ""
        year_m = re.search(r"(\d{4})\s*(?:года?|г\.)\s*выпуск", title, re.I)
        if year_m:
            year = year_m.group(1)

        price = None
        price_m = re.search(r"начальная цена\s*([\d\s]+)\s*руб", title, re.I)
        if price_m:
            try:
                price = float(price_m.group(1).replace(" ", "").replace("\xa0", ""))
            except ValueError:
                pass
        if price is None:
            price_m2 = re.findall(r"([\d\s]+)\s*(?:руб|₽)", text)
            for pm in price_m2:
                try:
                    val = float(pm.replace(" ", "").replace("\xa0", ""))
                    if val > 1000:
                        price = val
                        break
                except ValueError:
                    pass

        imgs = []
        for m in re.finditer(r'src="(/upload/[^"]+\.(?:jpg|jpeg|png|webp))"', text):
            img = m.group(1)
            if img not in imgs and "icon" not in img.lower() and "logo" not in img.lower():
                imgs.append(img)

        slug = path.rstrip("/").split("/")[-1]
        lot_id_match = re.search(r"^([a-z0-9]+-[a-z0-9]+)", slug)
        lot_no = lot_id_match.group(1).upper() if lot_id_match else slug[:30]

        return {
            "efrsb_id": f"auction-house:{lot_no}",
            "lot_no": lot_no,
            "title": title,
            "vin": vin or None,
            "make_model": (f"{make_model} {year}".strip() if year else make_model) or None,
            "price_amount": price,
            "price_currency": "RUB",
            "source_url": url,
            "platform_lot_url": url,
            "raw": {"images": [f"https://auction-house.ru{img}" for img in imgs[:40]], "platform": "auction-house.ru"},
        }
    except Exception as e:
        log.debug("auction-house lot %s: %s", path, e)
        return None


def extract_platform_images(lot_data: dict) -> list[str]:
    """Извлечь URL изображений из данных лота (парсинг площадки)."""
    raw = lot_data.get("raw") or {}
    return raw.get("images", [])


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    lot = {
        "vin": sys.argv[1] if len(sys.argv) > 1 else "XW8ZZZ5N8JG012345",
        "make_model": sys.argv[2] if len(sys.argv) > 2 else "Volkswagen Tiguan",
        "title": sys.argv[3] if len(sys.argv) > 3 else "Легковой автомобиль Volkswagen Tiguan",
        "lot_no": "1",
    }
    domain = sys.argv[4] if len(sys.argv) > 4 else "utender.ru"
    url, imgs = find_platform_photos(lot, domain)
    print("page:", url)
    for i in imgs[:20]:
        print("  ", i)
