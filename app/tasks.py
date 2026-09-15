"""Фоновые задачи: прогон скрейпинга и поиск фото. Один поток воркера."""
from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime, timedelta

from app.config import Settings, settings
from app.db import finish_run, init_db, start_run, update_run
from app.scraper.efrsb import EFRSBScraper, store_lots
from app.photos.search import find_photos_for_lot

log = logging.getLogger("efrsb.tasks")

_lock = threading.Lock()
_state: dict = {"job": None, "running": False, "last": None}


def scrape_job(
    date_from: str = "",
    date_to: str = "",
    max_pages: int | None = None,
    find_photos: bool = True,
    all_active: bool = False,
    s: Settings | None = None,
) -> dict:
    """Запуск скрейпинга в фоне. Возвращает id задания."""
    global _state
    with _lock:
        if _state["running"]:
            return {"job": _state["job"], "already_running": True}
        run_id = start_run()
        _state.update({"running": True, "job": run_id})
    s = s or settings
    threading.Thread(
        target=_run_job,
        args=(run_id, date_from, date_to, max_pages, find_photos, all_active, s),
        daemon=True,
        name="efrsb-scrape",
    ).start()
    return {"job": run_id, "already_running": False}


def _run_job(
    run_id: int,
    date_from: str,
    date_to: str,
    max_pages: int | None,
    find_photos: bool,
    all_active: bool,
    s: Settings,
) -> None:
    global _state
    try:
        update_run(run_id, message="старт скрейпинга")
        max_pages = max_pages or s.max_pages

        if all_active:
            date_from = ""
            date_to = ""
        scraper = EFRSBScraper(s)
        lots = scraper.scrape_live(date_from=date_from, date_to=date_to)
        found, new = store_lots(lots)

        update_run(run_id, lots_found=found, lots_new=new, message=f"найдено лотов: {found}, новых: {new}")

        photos_found = 0
        if find_photos:
            # обработка лотов с VIN и без фото
            from app.db import list_lots

            page = 1
            while True:
                lots, total, _ = list_lots(has_photo=False, page_size=30, page=page)
                if not lots:
                    break
                for lot in lots:
                    if not lot.get("vin"):
                        continue
                    res = find_photos_for_lot(lot["id"], s)
                    photos_found += res.get("downloaded", 0)
                page += 1
                if page * 30 >= total:
                    break

        finish_run(run_id, "done", f"готово. лотов {found}, новых {new}, фото {photos_found}",
                   lots_found=found, lots_new=new, photos_found=photos_found)
    except Exception as e:  # noqa: BLE001
        log.exception("job failed")
        finish_run(run_id, "error", f"{e}\n{traceback.format_exc()[-2000:]}")
    finally:
        with _lock:
            _state["running"] = False
            _state["last"] = run_id


def scrape_platforms(
    platforms: list[str] | None = None,
    find_photos: bool = True,
    s: Settings | None = None,
) -> dict:
    """Запуск скрейпинга площадок в фоне."""
    global _state
    with _lock:
        if _state["running"]:
            return {"job": _state["job"], "already_running": True}
        run_id = start_run()
        _state.update({"running": True, "job": run_id})
    s = s or settings
    threading.Thread(
        target=_run_platforms_job,
        args=(run_id, platforms, find_photos, s),
        daemon=True,
        name="efrsb-platforms",
    ).start()
    return {"job": run_id, "already_running": False}


def _run_platforms_job(
    run_id: int,
    platforms: list[str] | None,
    find_photos: bool,
    s: Settings,
) -> None:
    global _state
    try:
        update_run(run_id, message="старт скрейпинга площадок")
        from app.scraper.platforms import scrape_lot_online, scrape_auction_house
        from app.db import upsert_lot, add_photo, set_lot_props

        all_platforms = platforms or ["lot-online.ru", "auction-house.ru"]
        total_found = 0
        total_new = 0
        total_photos = 0

        for plat in all_platforms:
            update_run(run_id, message=f"парсинг {plat}...")
            try:
                if plat == "lot-online.ru":
                    lots = scrape_lot_online()
                elif plat == "auction-house.ru":
                    lots = scrape_auction_house()
                else:
                    log.warning("неизвестная площадка: %s", plat)
                    continue

                for lot_data in lots:
                    lot_id, is_new = upsert_lot(lot_data)
                    total_found += 1
                    if is_new:
                        total_new += 1

                    # Сохраняем фото с площадки
                    images = (lot_data.get("raw") or {}).get("images", [])
                    for img_url in images:
                        if img_url:
                            add_photo(lot_id, img_url, source_site=plat, status="new")

                    # Дополнительный поиск фото по VIN через поисковики
                    if find_photos and lot_data.get("vin"):
                        try:
                            find_photos_for_lot(lot_id, s)
                            total_photos += 1
                        except Exception as e:
                            log.debug("photo search %s: %s", lot_data.get("vin"), e)

                update_run(run_id, message=f"{plat}: найдено {len(lots)} лотов")
            except Exception as e:
                log.warning("ошибка площадки %s: %s", plat, e)

        finish_run(run_id, "done",
                   f"готово. площадок {len(all_platforms)}, лотов {total_found}, новых {total_new}, фото {total_photos}",
                   lots_found=total_found, lots_new=total_new, photos_found=total_photos)
    except Exception as e:
        log.exception("platforms job failed")
        finish_run(run_id, "error", f"{e}\n{traceback.format_exc()[-2000:]}")
    finally:
        with _lock:
            _state["running"] = False
            _state["last"] = run_id


def scrape_status() -> dict:
    with _lock:
        running = _state["running"]
        job = _state["job"]
    from app.db import last_run

    lr = last_run()
    return {"running": running, "job": job, "last_run": lr}


# ---------------------------------------------------------------------------
# Торги-россии.рф
# ---------------------------------------------------------------------------
def scrape_torgi_job(
    max_pages: int | None = None,
    fetch_details: bool = True,
    find_photos: bool = True,
    s: Settings | None = None,
) -> dict:
    """Запуск скрейпинга торгов-россии.рф в фоне."""
    global _state
    with _lock:
        if _state["running"]:
            return {"job": _state["job"], "already_running": True}
        run_id = start_run()
        _state.update({"running": True, "job": run_id})
    s = s or settings
    threading.Thread(
        target=_run_torgi_job,
        args=(run_id, max_pages or s.torgi_max_pages, fetch_details, find_photos, s),
        daemon=True,
        name="torgi-scrape",
    ).start()
    return {"job": run_id, "already_running": False}


def _run_torgi_job(
    run_id: int,
    max_pages: int,
    fetch_details: bool,
    find_photos: bool,
    s: Settings,
) -> None:
    global _state
    try:
        update_run(run_id, message="старт скрейпинга торгов-россии.рф")
        from app.scraper.torgi import scrape_torgi, store_torgi_lots

        lots = scrape_torgi(max_pages=max_pages, fetch_details=fetch_details)
        found, new = store_torgi_lots(lots)

        update_run(run_id, lots_found=found, lots_new=new,
                   message=f"торги-россии: лотов {found}, новых {new}")

        photos_found = 0
        if find_photos:
            from app.db import list_lots
            page = 1
            while True:
                lots_p, total, _ = list_lots(has_photo=False, page_size=30, page=page,
                                              source="торги-россии")
                if not lots_p:
                    break
                for lot in lots_p:
                    if not lot.get("vin"):
                        continue
                    res = find_photos_for_lot(lot["id"], s)
                    photos_found += res.get("downloaded", 0)
                page += 1
                if page * 30 >= total:
                    break

        finish_run(run_id, "done",
                   f"торги-россии готово. лотов {found}, новых {new}, фото {photos_found}",
                   lots_found=found, lots_new=new, photos_found=photos_found)
        from app.db import add_event
        add_event("scrape", f"Скрейпинг торгов-России: лотов {found}, новых {new}, фото {photos_found}")
    except Exception as e:
        log.exception("torgi job failed")
        finish_run(run_id, "error", f"{e}\n{traceback.format_exc()[-2000:]}")
    finally:
        with _lock:
            _state["running"] = False
            _state["last"] = run_id


# ---------------------------------------------------------------------------
# Email-рассылка
# ---------------------------------------------------------------------------
def email_send_job(s: Settings | None = None) -> dict:
    """Отправить запросы управляющим по всем подходящим лотам."""
    global _state
    with _lock:
        if _state["running"]:
            return {"job": _state["job"], "already_running": True}
        run_id = start_run()
        _state.update({"running": True, "job": run_id})
    s = s or settings
    threading.Thread(
        target=_run_email_send,
        args=(run_id, s),
        daemon=True,
        name="email-send",
    ).start()
    return {"job": run_id, "already_running": False}


def _run_email_send(run_id: int, s: Settings) -> None:
    global _state
    try:
        update_run(run_id, message="отправка email-запросов")
        from app.email_worker import do_send_emails
        from app.db import add_event

        res = do_send_emails(s)
        if res.get("skipped") == "smtp_not_configured":
            finish_run(run_id, "error", "Email не сконфигурирован (нет SMTP настроек)")
            return
        sent = res.get("sent", 0)
        resent = res.get("resent", 0)
        errors = res.get("errors", 0)
        msg = f"email: отправлено {sent}, повторно {resent}, ошибок {errors}"
        finish_run(run_id, "done", msg, lots_found=sent + resent, lots_new=errors)
        add_event("email_batch", f"Рассылка: отправлено {sent}, повторно {resent}, ошибок {errors}")
    except Exception as e:
        log.exception("email send job failed")
        finish_run(run_id, "error", f"{e}\n{traceback.format_exc()[-2000:]}")
    finally:
        with _lock:
            _state["running"] = False
            _state["last"] = run_id


def email_check_job(s: Settings | None = None) -> dict:
    """Проверить входящие ответы и обработать их."""
    global _state
    with _lock:
        if _state["running"]:
            return {"job": _state["job"], "already_running": True}
        run_id = start_run()
        _state.update({"running": True, "job": run_id})
    s = s or settings
    threading.Thread(
        target=_run_email_check,
        args=(run_id, s),
        daemon=True,
        name="email-check",
    ).start()
    return {"job": run_id, "already_running": False}


def _run_email_check(run_id: int, s: Settings) -> None:
    global _state
    try:
        update_run(run_id, message="проверка входящих email")
        from app.email_worker import do_check_emails

        res = do_check_emails(s)
        if res.get("skipped") == "imap_not_configured":
            finish_run(run_id, "error", "IMAP не сконфигурирован")
            return
        finish_run(
            run_id, "done",
            f"email-проверка: ответов {res.get('responses', 0)}, обработано {res.get('processed', 0)}, "
            f"недоставлено {res.get('bounced', 0)}",
            lots_found=res.get("responses", 0), lots_new=res.get("processed", 0),
        )
    except Exception as e:
        log.exception("email check job failed")
        finish_run(run_id, "error", f"{e}\n{traceback.format_exc()[-2000:]}")
    finally:
        with _lock:
            _state["running"] = False
            _state["last"] = run_id
