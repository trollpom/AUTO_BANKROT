"""Оркестратор: поиск фото лота по VIN и загрузка их в хранилище."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.config import Settings
from app.db import add_photo, get_lot, set_lot_props
from app.photos.download import download_photo
from app.photos.engines import search_all

log = logging.getLogger("efrsb.photos.search")


def build_queries(lot: dict, max_q: int = 4) -> list[str]:
    """Строит поисковые запросы на основе VIN + марки/модели + года."""
    vin = (lot.get("vin") or "").strip().upper()
    mm = (lot.get("make_model") or "").strip()
    title = (lot.get("title") or "").strip()
    queries: list[str] = []

    if vin:
        queries.append(vin)
        if mm:
            queries.append(f"{vin} {mm}")
            queries.append(f"{mm} VIN {vin}")
        else:
            queries.append(f"{vin} автомобиль")
    elif mm:
        queries.append(mm)
    if title and len(title) > 12:
        queries.append(title)
    seen: list[str] = []
    for q in queries:
        if q and q not in seen:
            seen.append(q)
    return seen[:max_q]


def find_photos_for_lot(lot_id: int, settings: Settings) -> dict:
    """Поиск + скачивание. Возвращает статистику."""
    lot = get_lot(lot_id)
    if not lot:
        return {"lot_id": lot_id, "error": "лот не найден"}

    queries = build_queries(lot)
    hits: list = []
    for q in queries:
        hits.extend(search_all(q, settings.photo_engines, limit=settings.photos_per_lot))

    # приоритет: фото с площадки торгов (это реальные фото лота)
    platform_imgs: list[str] = []
    platform_page = ""
    domain = (lot.get("trade_platform") or "").strip()
    if domain:
        try:
            from app.scraper.platforms import find_platform_photos

            page_url, imgs = find_platform_photos(
                lot, domain, direct_url=(lot.get("platform_lot_url") or "")
            )
            if page_url:
                platform_page = page_url
                set_lot_props(lot_id, platform_lot_url=page_url)
            platform_imgs = imgs
        except Exception as e:  # noqa: BLE001
            log.debug("площадка %s: %s", domain, e)

    # дедупликация и ограничение (фото площадки — первыми)
    seen_urls: set[str] = set()
    candidates: list = []

    def push(h) -> bool:
        if h.url in seen_urls:
            return False
        if len(candidates) >= settings.photos_per_lot:
            return False
        seen_urls.add(h.url)
        candidates.append(h)
        return True

    from app.photos.engines import ImageHit

    for u in platform_imgs:
        push(ImageHit(url=u, engine="platform", source_site=domain, source_page=platform_page))
    for h in hits:
        push(h)

    ok, failed, existing = 0, 0, 0
    with ThreadPoolExecutor(max_workers=settings.download_concurrency) as ex:
        for h in candidates:
            _, st = ex.submit(
                download_photo,
                lot_id,
                h.url,
                settings,
                h.source_site,
                h.source_page,
                h.title,
            ).result()
            if st == "ok":
                ok += 1
            elif st == "existing":
                existing += 1
            else:
                failed += 1
    return {
        "lot_id": lot_id,
        "vin": lot.get("vin"),
        "queries": queries,
        "platform": {"site": domain, "page": platform_page, "images_found": len(platform_imgs)},
        "candidates": len(candidates),
        "downloaded": ok,
        "failed": failed,
        "existing_skipped": existing,
    }


def fill_photos(fill_with_photos: bool, settings: Settings, max_photos_total: int = 200) -> dict:
    """Пройти по лотам без фото и заполнить фото."""
    from app.db import list_lots

    results = {"checked": 0, "with_photo": 0, "downloaded": 0, "errors": 0}
    processed = 0
    page = 1
    while processed < max_photos_total:
        lots, total, _ = list_lots(has_photo=False, page_size=50, page=page)
        if not lots:
            break
        for lot in lots:
            if processed >= max_photos_total:
                break
            processed += 1
            try:
                res = find_photos_for_lot(lot["id"], settings)
                results["checked"] += 1
                if res.get("downloaded"):
                    results["with_photo"] += 1
                    results["downloaded"] += res["downloaded"]
            except Exception as e:  # noqa: BLE001
                log.warning("нет фото для лота %s: %s", lot.get("id"), e)
                results["errors"] += 1
        if processed >= max_photos_total or page * 50 >= total:
            break
        page += 1
    return results