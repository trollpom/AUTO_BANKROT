"""Парсинг торгов-россии.рф — лоты с легковым транспортом.

Сайт работает на Next.js App Router с React Server Components.
Данные лотов встроены в RSC-payload (self.__next_f.push) как JSON.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger("efrsb.torgi")

BASE = "https://торги-россии.рф"
API_BASE = "https://xn--80aqu.xn----etbpba5admdlad.xn--p1ai"
LISTING_PATH = "/купить-легковой-транспорт-с-торгов"
CATEGORY_ID = 2  # Легковой транспорт

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def _session() -> requests.Session:
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


def _extract_rsc_chunks(html: str) -> str:
    """Собрать все RSC-payload чанки из self.__next_f.push() и объединить.

    Каждый чанк — JSON-строка, закодированная внутри вызова push.
    После объединения получаем поток данных React Flight (почти JSON).
    """
    chunks = re.findall(r'__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html)
    if not chunks:
        # Попытка 2: без строгой обёртки
        chunks = re.findall(r'__next_f\.push\(\[1,"((?:[^"\\]|\\.)*?)"\]\)', html)
    out = []
    for chunk in chunks:
        try:
            # Раскодировать как JSON-строку
            decoded = json.loads('"' + chunk + '"')
            out.append(decoded)
        except json.JSONDecodeError:
            out.append(chunk)
    return "".join(out)


def _extract_lots_from_rsc(html: str) -> list[dict]:
    """Извлечь массив initialLots из RSC-payload страницы."""
    text = _extract_rsc_chunks(html)
    if not text:
        log.warning("RSC-чанки не найдены на странице")
        return []

    m = re.search(r'"initialLots"\s*:\s*(\[)', text)
    if not m:
        # Может быть без кавычек-escaped
        idx = text.find("initialLots")
        if idx == -1:
            log.warning("initialLots не найден в RSC-payload")
            return []
        m = re.search(r'initialLots\s*:\s*(\[)', text[idx:])
        if not m:
            log.warning("initialLots не найден в RSC-payload")
            return []
        arr_start = idx + m.start(1)
    else:
        arr_start = m.start(1)

    try:
        decoder = json.JSONDecoder()
        lots, end = decoder.raw_decode(text[arr_start:])
        log.info("извлечено %d лотов из RSC-payload", len(lots))
        return lots
    except json.JSONDecodeError as e:
        log.error("ошибка парсинга initialLots: %s", e)
        return []


def _extract_meta_from_rsc(html: str) -> dict:
    """Извлечь initialMeta (пагинация) из RSC-payload."""
    text = _extract_rsc_chunks(html)
    if not text:
        return {}
    m = re.search(r'"initialMeta"\s*:\s*(\{)', text)
    if not m:
        idx = text.find("initialMeta")
        if idx == -1:
            return {}
        m = re.search(r'initialMeta\s*:\s*(\{)', text[idx:])
        if not m:
            return {}
        obj_start = idx + m.start(1)
    else:
        obj_start = m.start(1)
    try:
        decoder = json.JSONDecoder()
        meta, end = decoder.raw_decode(text[obj_start:])
        return meta
    except json.JSONDecodeError:
        return {}


def _fetch_page(sess: requests.Session, page: int = 1) -> tuple[list[dict], dict]:
    """Загрузить одну страницу каталога."""
    url = f"{BASE}{LISTING_PATH}"
    if page > 1:
        url += f"?page={page}"
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200:
            log.warning("torgi page %d: HTTP %s", page, r.status_code)
            return [], {}
        lots = _extract_lots_from_rsc(r.text)
        meta = _extract_meta_from_rsc(r.text)
        return lots, meta
    except Exception as e:
        log.error("torgi page %d: %s", page, e)
        return [], {}


def _fetch_lot_detail(sess: requests.Session, lot_id: int) -> dict | None:
    """Загрузить детальную информацию лота со страницы /lot/{id}."""
    url = f"{BASE}/lot/{lot_id}"
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200:
            log.warning("lot detail %d: HTTP %s", lot_id, r.status_code)
            return None
        return _extract_lot_from_detail_rsc(r.text, lot_id)
    except Exception as e:
        log.error("lot detail %d: %s", lot_id, e)
        return None


def _resolve_refs(obj, rsc_text: str) -> dict:
    """Разрешить RSC-ссылки вида '$7c' в значениях объекта.

    Ссылка '$X' указывает на чанк 'X:T<id>,<text>' — берём текст после запятой.
    """
    def _lookup(ref: str) -> str:
        ref = ref.lstrip("$").strip()
        # Ищем 'X:' в RSC-тексте
        m = re.search(rf"(?m)(?:^|[\n\\]){re.escape(ref)}:([^\n]*)", rsc_text)
        if not m:
            m = re.search(rf"{re.escape(ref)}:([^\n]*)", rsc_text)
        if not m:
            return ""
        val = m.group(1)
        # Чанк вида 'T1674,<text>' или '<text>'
        if val.startswith("T") and "," in val:
            val = val.split(",", 1)[1]
        return val.strip()

    def _walk(o):
        if isinstance(o, dict):
            return {k: _walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_walk(v) for v in o]
        if isinstance(o, str) and o.startswith("$") and len(o) <= 5:
            resolved = _lookup(o)
            if resolved:
                return resolved
            return o
        return o

    return _walk(obj)


def _extract_lot_from_detail_rsc(html: str, lot_id: int) -> dict | None:
    """Извлечь объект lot из RSC-payload страницы лота."""
    text = _extract_rsc_chunks(html)
    if not text:
        return None

    # На странице лота: объект lot содержится в пропсах компонента $L7b
    m = re.search(r'"lot"\s*:\s*(\{)', text)
    if not m:
        idx = text.find("lot")
        if idx == -1:
            return None
        m = re.search(r'lot\s*:\s*(\{)', text[idx:])
        if not m:
            log.warning("объект lot не найден на странице лота %d", lot_id)
            return None
        obj_start = idx + m.start(1)
    else:
        obj_start = m.start(1)

    try:
        decoder = json.JSONDecoder()
        lot_obj, end = decoder.raw_decode(text[obj_start:])
        if "id" not in lot_obj or lot_obj["id"] != lot_id:
            log.debug("id в объекте lot (%s) не совпадает с %s", lot_obj.get("id"), lot_id)
        # Разрешаем RSC-ссылки ($7c -> текст)
        lot_obj = _resolve_refs(lot_obj, text)
        return lot_obj
    except json.JSONDecodeError as e:
        log.error("ошибка парсинга лота %d: %s", lot_id, e)
        return None


def _extract_contacts(text: str, phone: str = "", email: str = "", name: str = "") -> tuple[str, str, str]:
    """Извлечь email/телефон/имя из текста «Порядок ознакомления»."""
    if not text:
        return phone, email, name

    # E-mail
    if not email:
        m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
        if m:
            email = m.group(0).strip(".,; ")

    # Телефон: т. 89049679627 | тел. +7... | 8 (904) 967-96-27
    if not phone:
        patterns = [
            r"(?:т\.|тел\.?|телефон|звонить|обращаться)\s*[:)\-]?\s*(\+?[78][\s\-\(\)\d]{9,16}\d)",
            r"(?:\+?[78][\s\-\(\)]{0,3}\d{3}[\s\-\(\)]{0,3}\d{3}[\s\-]{0,2}\d{2}[\s\-]{0,2}\d{2})",
            r"(\d{10,11})",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                cand = m.group(1) if m.lastindex else m.group(0)
                digits = re.sub(r"\D", "", cand)
                if len(digits) >= 10:
                    phone = cand.strip()
                    break

    # Имя управляющего: исходим из того что имя уже есть в поле trustee,
    # но если нет — ищем «управляющий ФИО»
    if not name:
        # Примеры: "Финансовый управляющий Шакирова Светлана Равильевна"
        #          "арбитражный управляющий Петров Иван Сергеевич"
        m = re.search(
            r"(?:финансовый|арбитражный)\s+управляющий[ая]?\s+([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)",
            text,
        )
        if m:
            name = m.group(1).strip()
        else:
            m = re.search(
                r"([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)\s*\(ИНН",
                text,
            )
            if m:
                name = m.group(1).strip()

    return phone, email, name


def _normalize_lot(raw: dict) -> dict:
    """Привести данные лота к формату БД."""
    from app.parser import extract_vins, detect_make_model, detect_category

    title = raw.get("title", "")
    information = raw.get("information", "")
    full_text = f"{title} {information}"

    # VIN
    vins = extract_vins(full_text)
    vin = vins[0] if vins else (raw.get("vins", [None])[0] if raw.get("vins") else None)

    # Марка/модель
    make_model = detect_make_model(title)

    # Категория (уже知道 — легковой транспорт)
    category = "Легковые автомобили"

    # Дебитор
    debtor_obj = raw.get("debtor") or {}
    debtor = debtor_obj.get("name", "") if isinstance(debtor_obj, dict) else raw.get("debtor_name", "")
    debtor_inn = debtor_obj.get("inn", "") if isinstance(debtor_obj, dict) else raw.get("debtor_inn_text", "")

    # Управляющий
    admin_obj = raw.get("arbitration_administrator") or {}
    trustee = admin_obj.get("name", "") if isinstance(admin_obj, dict) else raw.get("trustee", "")

    # Регион
    region_obj = raw.get("region") or {}
    if isinstance(region_obj, dict):
        region = region_obj.get("title", "")
    else:
        region = raw.get("region_title", "")

    # Площадка
    marketplace_obj = raw.get("marketplace") or {}
    if isinstance(marketplace_obj, dict):
        marketplace = marketplace_obj.get("small_title", "")
    else:
        marketplace = raw.get("marketplace_title", "")

    # Тип торгов
    trade_type_obj = raw.get("trade_type") or raw.get("trades_section") or {}
    if isinstance(trade_type_obj, dict):
        trade_type = trade_type_obj.get("title", "")
    else:
        trade_type = raw.get("trade_type_title", "")

    # Форма торгов
    trade_form = raw.get("trade_form", "")

    # Цены
    start_price = raw.get("start_price")
    current_price = raw.get("current_price")

    # Статус
    status_obj = raw.get("status") or {}
    if isinstance(status_obj, dict):
        status_title = status_obj.get("title", "")
    else:
        status_title = str(status_obj)

    # Даты
    stages = raw.get("stages") or {}
    publish_date = stages.get("begin_bid_time", "")
    end_date = stages.get("end_bid_time", "")

    # Контакты
    contact_phone = raw.get("conact_phone")  # опечатка на сайте
    contact_email = raw.get("conact_email")
    contact_name = raw.get("conact_name")

    # Порядок ознакомления
    acquaintance_order = raw.get("acquaintance_order", "")
    bid_application_order = raw.get("bid_application_order", "")

    # Если контактов нет — вытаскиваем из текста порядка ознакомления
    if not contact_phone or not contact_email or not contact_name:
        contact_phone, contact_email, contact_name = _extract_contacts(acquaintance_order, contact_phone, contact_email, contact_name)

    # Документы
    documents = raw.get("documents") or []

    # Ссылки
    source_links = raw.get("source_links") or []
    source_url = ""
    for link in source_links:
        if "bankrot.fedresurs.ru" in (link.get("link", "") or ""):
            source_url = link["link"]
            break

    return {
        "efrsb_id": f"torgi:{raw.get('id', '')}",
        "lot_no": str(raw.get("id", "")),
        "title": title,
        "description": information or title,
        "debtor": debtor,
        "debtor_inn": debtor_inn,
        "trustee": trustee,
        "organizer": marketplace,
        "sale_method": trade_form or trade_type,
        "category": category,
        "vin": vin,
        "make_model": make_model,
        "price_amount": current_price or start_price,
        "price_currency": "RUB",
        "start_price": start_price,
        "current_price": current_price,
        "region": region,
        "address": "",
        "publish_date": publish_date,
        "end_date": end_date,
        "trade_platform": f"торги-россии.рф ({marketplace})" if marketplace else "торги-россии.рф",
        "platform_lot_url": f"{BASE}/lot/{raw.get('id', '')}",
        "source_url": source_url or f"{BASE}/lot/{raw.get('id', '')}",
        "status": status_title or "published",
        "trade_type": trade_type,
        "trade_form": trade_form,
        "marketplace": marketplace,
        "contact_phone": contact_phone,
        "contact_email": contact_email,
        "contact_name": contact_name,
        "acquaintance_order": acquaintance_order,
        "bid_application_order": bid_application_order,
        "days_remaining": raw.get("days_remaining", ""),
        "price_direction": raw.get("price_direction", ""),
        "documents": documents,
        "raw": raw,
    }


def scrape_torgi(
    max_pages: int = 10,
    fetch_details: bool = True,
    workers: int = 6,
    start_page: int = 1,
) -> list[dict]:
    """Парсинг каталога торгов-россии.рф.

    Args:
        max_pages: максимальное кол-во страниц (от start_page)
        fetch_details: загружать ли страницу каждого лота для полной информации
        workers: сколько потоков использовать для загрузки деталей лотов
        start_page: с какой страницы начать (1 — первая)
    """
    sess = _session()
    all_lots = []

    for page in range(start_page, start_page + max_pages):
        log.info("torgi: страница %d/%d", page, max_pages)
        raw_lots, meta = _fetch_page(sess, page)
        if not raw_lots:
            log.info("torgi: нет лотов на странице %d, стоп", page)
            break

        last_page = meta.get("last_page", max_pages)
        if page >= last_page:
            log.info("torgi: достигнута последняя страница %d", last_page)

        page_raw = [r for r in raw_lots if r.get("id")]

        # Загружаем детали параллельно
        details: dict[str, dict] = {}
        if fetch_details and page_raw:
            ids = [str(r["id"]) for r in page_raw]

            def _load(lid: str) -> tuple[str, dict]:
                s = _session()
                try:
                    return lid, _fetch_lot_detail(s, lid)
                except Exception:  # noqa: BLE001
                    return lid, {}
                finally:
                    s.close()

            with ThreadPoolExecutor(max_workers=workers) as ex:
                for lid, det in ex.map(_load, ids):
                    if det:
                        details[lid] = det
            time.sleep(0.5)

        for raw_lot in page_raw:
            lot_id = str(raw_lot.get("id"))

            # Нормализуем: приоритет у детальных данных
            source = details.get(lot_id) or raw_lot
            normalized = _normalize_lot(source)
            # Дополняем из listings если чего-то нет в detail
            if not normalized.get("price_amount"):
                normalized["price_amount"] = raw_lot.get("current_price") or raw_lot.get("start_price")
            all_lots.append(normalized)

        log.info("torgi: итого %d лотов со стр. %d", len(all_lots), page)
        if page >= last_page:
            break
        time.sleep(1.5)  # пауза между страницами

    log.info("torgi: всего собрано %d лотов", len(all_lots))
    return all_lots


def store_torgi_lots(lots: list[dict], download_pictures: bool = True) -> tuple[int, int]:
    """Сохранить лоты в БД. Возвращает (found, new)."""
    from app.db import upsert_lot, add_photo, add_event
    from app.photos.download import download_photo
    from app.config import settings

    found = 0
    new = 0
    for lot_data in lots:
        lot_id, is_new = upsert_lot(lot_data)
        found += 1
        if is_new:
            new += 1
        if is_new:
            add_event("lot_new", f"Новый лот №{lot_data.get('lot_no')}: {(lot_data.get('title') or '')[:70]}", lot_id)

        # Фотографии (если есть в pictures)
        raw = lot_data.get("raw") or {}
        pictures = raw.get("pictures") or []
        photos_ok = 0
        for pic in pictures:
            # pictures может быть списком строк URL или списком dict
            if isinstance(pic, dict):
                pic_url = pic.get("url") or pic.get("src") or pic.get("link") or ""
            else:
                pic_url = pic or ""
            if not pic_url:
                continue
            if pic_url.startswith("/"):
                pic_url = f"{BASE}{pic_url}"
            if download_pictures:
                _, status = download_photo(lot_id, pic_url, settings, source_site="торги-россии.рф")
                if status == "ok":
                    photos_ok += 1
            else:
                add_photo(lot_id, pic_url, source_site="торги-россии.рф", status="new")
        if photos_ok:
            add_event("photos", f"Добавлено фото ({photos_ok}) по лоту №{lot_data.get('lot_no')}", lot_id)

    return found, new


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    lots = scrape_torgi(max_pages=pages, fetch_details=True)
    for lot in lots[:5]:
        print(f"  {lot['lot_no']}: {lot['title'][:60]}... | {lot['price_amount']} ₽ | {lot['vin'] or 'нет VIN'}")
    print(f"\nИтого: {len(lots)} лотов")


def refresh_torgi_statuses(refresh_days: int = 7, workers: int = 6) -> dict:
    """Обновить статусы и текущие цены лотов торгов-россии.рф по detail-страницам.

    Выбирает активные лоты (идёт приём/торги объявлены), которые:
      - заканчиваются в ближайшие refresh_days (+ уже завершённые по end_date);
      - либо являются публичными предложениями (у них цена меняется ежедневно).
    Обновляет статус/цены в БД, пишет точки в price_history для публичных
    предложений, после чего пересчитывает days_remaining для всех лотов.
    """
    from datetime import datetime, timedelta
    from app.db import q, upsert_lot, recompute_lot_timings, add_event, tx, utcnow
    from app.db import record_price, seed_price_history

    now = datetime.now()
    future = now + timedelta(days=refresh_days)
    future_str = future.isoformat(timespec="seconds")

    rows = q(
        """SELECT id, lot_no, end_date, trade_form FROM lots
           WHERE trade_platform LIKE '%торги-россии%'
             AND (status LIKE '%заявок%' OR status LIKE '%объявлены%')
             AND (
                 (end_date IS NOT NULL AND end_date != '' AND end_date <= ?)
                 OR trade_form LIKE '%публичного предложения%'
             )""",
        (future_str,),
    )
    if not rows:
        return {"checked": 0, "status_changed": 0, "price_changed": 0}

    checked = 0
    status_changed = 0
    price_changed = 0

    def _refresh_one(lot_no: str) -> tuple[dict | None, dict]:
        s = _session()
        try:
            time.sleep(random.uniform(0.3, 0.7))
            detail = _fetch_lot_detail(s, int(lot_no))
            if not detail:
                return None, {}
            norm = _normalize_lot(detail)
            old_lot = q(
                "SELECT status, current_price, trade_form FROM lots "
                "WHERE lot_no=? AND trade_platform LIKE '%торги-россии%'",
                (lot_no,),
            )
            old_status = old_lot[0]["status"] if old_lot else ""
            old_price = old_lot[0]["current_price"] if old_lot else None
            old_form = old_lot[0]["trade_form"] if old_lot else ""
            lot_id, _ = upsert_lot(norm)
            new_status = norm.get("status") or ""
            new_price = norm.get("current_price")
            new_form = norm.get("trade_form") or old_form
            is_public = "публичного предложения" in new_form.lower()
            if is_public:
                seed_price_history(lot_id, norm.get("start_price"), new_price, norm.get("publish_date"))
                # next_reduce_at: начало следующего ценового периода (или конец — если он последний)
                raw_obj = norm.get("raw") or {}
                if isinstance(raw_obj, str):
                    try:
                        raw_obj = json.loads(raw_obj)
                    except (TypeError, ValueError):
                        raw_obj = {}
                if isinstance(raw_obj, dict):
                    periods = raw_obj.get("bidding_periods") or []
                else:
                    periods = []
                cur = next((i for i, p in enumerate(periods) if p.get("is_current")), None)
                if cur is not None and cur + 1 < len(periods):
                    nxt = periods[cur + 1]
                    nra = (nxt.get("begin") or nxt.get("bid_end") or "").replace(" ", "T")
                else:
                    nra = (norm.get("end_date") or "").replace(" ", "T")
                with tx() as c:
                    c.execute(
                        "UPDATE lots SET next_reduce_at=?, updated_at=? WHERE id=?",
                        (nra or None, utcnow(), lot_id),
                    )
            return {
                "lot_no": lot_no,
                "id": lot_id,
                "old_status": old_status,
                "new_status": new_status,
                "old_price": old_price,
                "new_price": new_price,
                "is_public": is_public,
            }, {}
        finally:
            s.close()

    lot_ids = [r["lot_no"] for r in rows]
    log.info("refresh_torgi_statuses: проверяем %d лотов (горизонт %d дн. + публичные предложения)", len(lot_ids), refresh_days)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_refresh_one, lid): lid for lid in lot_ids if lid}
        for fut in as_completed(futs):
            lid = futs[fut]
            try:
                result, _ = fut.result()
            except Exception:  # noqa: BLE001
                log.error("refresh_torgi_statuses: ошибка лота %s", lid, exc_info=True)
                continue
            if not result:
                continue
            checked += 1

            if result["new_status"] and result["new_status"] != result.get("old_status"):
                status_changed += 1
                add_event(
                    "status_update",
                    f"Статус обновлён №{result['lot_no']}: «{result['old_status']}» → «{result['new_status']}»",
                    result["id"],
                )

            if result["is_public"] and result["new_price"] is not None:
                wrote = record_price(result["id"], result["new_price"])
                if result["new_price"] != result.get("old_price"):
                    price_changed += 1
                    pdir = result["new_price"] < (result.get("old_price") or result["new_price"]) and "price_down" or "price_up"
                    add_event(
                        "price_update",
                        f"Цена №{result['lot_no']}: {result['old_price']} → {result['new_price']} ₽"
                        f"{' (записана история)' if wrote else ''}",
                        result["id"],
                    )

    recompute_lot_timings()
    log.info(
        "refresh_torgi_statuses: проверено %d, статусов изменено %d, цен изменено %d",
        checked, status_changed, price_changed,
    )
    return {"checked": checked, "status_changed": status_changed, "price_changed": price_changed}
