"""Ежедневный автопарсинг торгов-россии.рф (поиск новых лотов).

Фоновый поток запускается вместе с приложением:
- первый запуск через TORGI_AUTO_SCRAPE_DELAY_MIN минут;
- далее каждые TORGI_SCRAPE_INTERVAL_HOURS часов;
- парсится TORGI_SCRAPE_PAGES страниц каталога (новые лоты — сверху),
  новые лоты и фото попадают в БД, события — в журнал.
"""
from __future__ import annotations

import logging
import threading
import time

from app.config import settings

log = logging.getLogger("efrsb.scrape_worker")

_worker_started = False


def _loop() -> None:
    delay = max(0, settings.torgi_auto_scrape_delay_min) * 60
    if delay:
        log.info("автопарсинг торгов-России: первый запуск через %d мин", int(delay // 60))
        time.sleep(delay)

    while True:
        try:
            from app.db import add_event
            from app.tasks import scrape_torgi_job

            pages = max(1, settings.torgi_scrape_pages)
            res = scrape_torgi_job(
                max_pages=pages,
                fetch_details=True,
                find_photos=False,
                s=settings,
            )
            if res.get("already_running"):
                log.info("автопарсинг торгов-России: уже выполняется, пропуск")
            else:
                add_event("scrape", f"Плановая проверка торгов-России: страниц {pages}")
                log.info("автопарсинг торгов-России: запущено (%d стр.)", pages)

            # Обновление статусов active лотов по detail-страницам (если включено)
            rdays = getattr(settings, "torgi_status_refresh_days", 7) or 0
            if rdays:
                try:
                    from app.scraper.torgi import refresh_torgi_statuses

                    stats = refresh_torgi_statuses(refresh_days=rdays)
                    if stats.get("checked"):
                        add_event(
                            "status_update",
                            f"Обновление статусов/цен: проверено {stats['checked']}, "
                            f"статусов {stats['status_changed']}, цен {stats['price_changed']}",
                        )
                except Exception:  # noqa: BLE001
                    log.exception("автопарсинг торгов-России: ошибка обновления статусов")

        except Exception:  # noqa: BLE001
            log.exception("автопарсинг торгов-России: сбой цикла")

        time.sleep(max(1, settings.torgi_scrape_interval_hours) * 3600)


def start_scrape_worker() -> None:
    """Запустить ежедневный автопарсинг (идемпотентно)."""
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    if not settings.torgi_auto_scrape:
        log.info("автопарсинг торгов-России отключён (TORGI_AUTO_SCRAPE=0)")
        return
    threading.Thread(target=_loop, daemon=True, name="torgi-scheduler").start()