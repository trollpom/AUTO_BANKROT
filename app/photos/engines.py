"""Движки поиска изображений по запросу (VIN / марка). Возвращают кандидатов на фото."""
from __future__ import annotations

import html as html_mod
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass, field

import requests

log = logging.getLogger("efrsb.photos.engines")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
HDRS = {
    "User-Agent": UA,
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}


@dataclass
class ImageHit:
    url: str
    title: str = ""
    source_page: str = ""
    source_site: str = ""
    width: int = 0
    height: int = 0
    engine: str = ""
    cookies: list = field(default_factory=list)


def _sess(timeout: int = 25) -> requests.Session:
    from app.config import settings

    s = requests.Session()
    s.headers.update(HDRS)
    s.trust_env = False
    if settings.proxies:
        s.proxies.update(settings.proxies)
    s.mount("https://", requests.adapters.HTTPAdapter(max_retries=1))
    s.mount("http://", requests.adapters.HTTPAdapter(max_retries=1))
    s._timeout = timeout  # type: ignore[attr-defined]
    return s


def _get(s: requests.Session, url: str, **kw) -> requests.Response:
    kw.setdefault("timeout", getattr(s, "_timeout", 25))
    return s.get(url, **kw)


def _site_of(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc
        return re.sub(r"^www\.", "", netloc)
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Yandex Images
# ---------------------------------------------------------------------------
_BAD_EXT = re.compile(r"\.(?:css|js|svg|ttf|woff2?|ico|json|xml|txt|html?|axd)(?:\?|$)", re.I)
_BAD_HOST = ("yastatic.net", "yandex.net", "yandex.ru/favicon", "mc.yandex", "jpegshare")


def search_yandex(query: str, limit: int = 20) -> list[ImageHit]:
    q = urllib.parse.quote(query)
    url = f"https://yandex.ru/images/search?text={q}&noreask=1"
    hits: list[ImageHit] = []
    try:
        r = _get(_sess(), url, headers={**HDRS, "Referer": "https://yandex.ru/"})
        if r.status_code != 200:
            log.debug("yandex %s -> %s", r.status_code, query)
            return hits
        body = html_mod.unescape(r.text)
        # блок "items[]" содержит реальные ссылки на изображения
        for m in re.finditer(r'"img_href"\s*:\s*"((?:[^"\\]|\\.)*)"', body):
            u = json.loads(f'"{m.group(1)}"')
            if not _is_usable_url(u):
                continue
            hits.append(
                ImageHit(
                    url=u,
                    title=_nearby_json(body, m.start(), "title"),
                    source_page=f"https://yandex.ru/images/search?text={q}",
                    source_site=_site_of(u),
                    engine="yandex",
                )
            )
            if len(hits) >= limit:
                break
    except Exception as e:  # noqa: BLE001
        log.debug("yandex error: %s", e)
    return _dedupe(hits)[:limit]


def _is_usable_url(u: str) -> bool:
    if not u.startswith("http"):
        return False
    low = u.lower()
    if _BAD_EXT.search(low) or any(h in low for h in _BAD_HOST):
        return False
    return True


def _nearby_json(body: str, pos: int, key: str) -> str:
    """Извлечь значение key из JSON-блока рядом с pos."""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"', body[pos:pos + 500])
    if m:
        try:
            return json.loads(f'"{m.group(1)}"')
        except Exception:  # noqa: BLE001
            return m.group(1)
    return ""


def _unquote(u: str) -> str:
    try:
        return urllib.parse.unquote(u)
    except Exception:  # noqa: BLE001
        return u


# ---------------------------------------------------------------------------
# Bing Images
# ---------------------------------------------------------------------------
def search_bing(query: str, limit: int = 20) -> list[ImageHit]:
    q = urllib.parse.quote(query)
    url = f"https://www.bing.com/images/search?q={q}&form=HDRSC3&adlt=off"
    hits: list[ImageHit] = []
    try:
        r = _get(_sess(), url, headers={**HDRS, "Referer": "https://www.bing.com/"})
        if r.status_code != 200:
            return hits
        body = html_mod.unescape(r.text)
        for m in re.finditer(r'"murl"\s*:\s*"((?:[^"\\]|\\.)*)"', body):
            u = json.loads(f'"{m.group(1)}"')
            if not _is_usable_url(u):
                continue
            hits.append(ImageHit(url=u, engine="bing", source_site=_site_of(u),
                                 source_page=url))
            if len(hits) >= limit:
                break
    except Exception as e:  # noqa: BLE001
        log.debug("bing error: %s", e)
    return _dedupe(hits)[:limit]


# ---------------------------------------------------------------------------
# DuckDuckGo Images (ajax)
# ---------------------------------------------------------------------------
_DDG_HDRS = {
    "User-Agent": UA,
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "sec-ch-ua": '"Chromium";v="125", "Google Chrome";v="125", "Not.A/Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def search_duckduckgo(query: str, limit: int = 20) -> list[ImageHit]:
    hits: list[ImageHit] = []
    q = urllib.parse.quote(query)
    try:
        s = _sess()
        page_url = f"https://duckduckgo.com/?q={q}&iax=images&ia=images"
        r0 = _get(s, page_url, headers=HDRS)
        vqd = _grab_vqd_from_page(s, q)
        if not vqd:
            log.debug("duckduckgo: vqd не получен")
            return hits
        r = _get(
            s,
            f"https://duckduckgo.com/i.js?l=ru-ru&o=json&q={q}&vqd={vqd}",
            headers={**_DDG_HDRS, "Referer": page_url},
        )
        if r.status_code != 200:
            log.debug("duckduckgo i.js -> %s", r.status_code)
            return hits
        data = r.json()
        for it in data.get("results", []):
            u = it.get("image")
            if not u:
                continue
            src_page = it.get("url", "")
            hits.append(
                ImageHit(
                    url=u,
                    title=html_mod.unescape(it.get("title", "")),
                    source_page=src_page,
                    source_site=_site_of(src_page),
                    width=it.get("width") or 0,
                    height=it.get("height") or 0,
                    engine="duckduckgo",
                )
            )
            if len(hits) >= limit:
                break
    except Exception as e:  # noqa: BLE001
        log.debug("duckduckgo error: %s", e)
    return _dedupe(hits)[:limit]


def _grab_vqd_from_page(s: requests.Session, q: str) -> str:
    try:
        r = _get(
            s,
            f"https://duckduckgo.com/?q={q}&iax=images&ia=images",
            headers={**HDRS, "Referer": "https://duckduckgo.com/",
                     "Sec-Fetch-Site": "same-origin"},
        )
        m = re.search(r'vqd=([\d-]+)', r.text)
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Avito (прямой источник фото по объявлениям)
# ---------------------------------------------------------------------------
def search_avito(query: str, limit: int = 8) -> list[ImageHit]:
    """avito — объявления часто содержат реальные фото автомобиля.
    Требует "чистого" IP/прокси: без него отдаёт 429 (доступ ограничен)."""
    hits: list[ImageHit] = []
    q = urllib.parse.quote(query)
    try:
        s = _sess()
        if not s.proxies:
            log.debug("avito: без прокси (EFRSB_PROXY) avito обычно отдаёт 429")
        r = _get(
            s,
            f"https://www.avito.ru/all/avtomobili?q={q}",
            headers={"User-Agent": UA, "Referer": "https://www.avito.ru/"},
        )
        if r.status_code == 429:
            log.debug("avito: 429 (ip blocked)")
            return hits
        if r.status_code != 200:
            return hits

        # основной путь: сериализованный стейт с imageUrls[]
        for m in re.finditer(r'"imageUrls"\s*:\s*\[(.*?)\]', r.text):
            urls = re.findall(r'"https?:[^"]+"', m.group(1))
            for u in urls:
                uu = json.loads(u)
                if uu.startswith("https://img.avito.st/"):
                    hits.append(ImageHit(url=uu, engine="avito", source_site="avito.ru",
                                         source_page=f"https://www.avito.ru/all/avtomobili?q={q}"))
                    if len(hits) >= limit:
                        return _dedupe(hits)[:limit]
        # фолбэк: маркеры карточек
        for m in re.finditer(r'data-marker="item-photo"[^>]*\bsrc="([^"]+)"', r.text):
            if m.group(1).startswith("https"):
                hits.append(ImageHit(url=m.group(1), engine="avito", source_site="avito.ru"))
                if len(hits) >= limit:
                    break
    except Exception as e:  # noqa: BLE001
        log.debug("avito error: %s", e)
    return _dedupe(hits)[:limit]


ENGINES = {
    "yandex": search_yandex,
    "bing": search_bing,
    "duckduckgo": search_duckduckgo,
    "avito": search_avito,
}


def search_all(query: str, engines: list[str], limit: int = 24) -> list[ImageHit]:
    per = max(2, limit // max(1, len(engines)))
    all_hits: list[ImageHit] = []
    for name in engines:
        fn = ENGINES.get(name)
        if not fn:
            continue
        try:
            hits = fn(query, limit=per)
            all_hits.extend(hits)
            log.info("[%s] '%s' -> %s фото", name, query, len(hits))
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] ошибка: %s", name, e)
    return _dedupe(all_hits, key="url")[:limit]


def _dedupe(hits: list[ImageHit], key: str = "url") -> list[ImageHit]:
    seen: set[str] = set()
    out: list[ImageHit] = []
    for h in hits:
        k = h.url if key == "url" else (h.url, h.title)
        if k in seen:
            continue
        seen.add(k)  # type: ignore[arg-type]
        # фильтруем мусор
        if any(bad in h.url.lower() for bad in ("data:", "spacer", "pixel.gif", "googleusercontent")):
            continue
        out.append(h)
    return out