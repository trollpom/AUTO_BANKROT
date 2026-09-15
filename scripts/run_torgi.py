"""CLI: парсинг торгов-россии.рф (легковой транспорт) и сохранение в БД.

Примеры:
  python scripts/run_torgi.py --pages 5
  python scripts/run_torgi.py --pages 50 --no-details
  python scripts/run_torgi.py --pages 209 --workers 8
  python scripts/run_torgi.py --x pages 5..209          # диапазон
  python scripts/run_torgi.py --pages 20 --send-email   # после скрейпинга отправить запросы
"""
from __future__ import annotations

import argparse
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("run_torgi")

from app.config import settings
from app.scraper.torgi import scrape_torgi, store_torgi_lots


def send_inquiries() -> None:
    """Отправить email-запросы управляющим по лотам без ответа."""
    from app.db import get_lots_for_email, set_email_status, utcnow
    from app.emailer import get_email_service

    emailer = get_email_service()
    if not emailer.is_configured():
        log.warning("Email не сконфигурирован. Укажите EMAIL_SMTP_* в .env")
        return

    lots_to_email = get_lots_for_email(limit=200)
    sent = 0
    for lot in lots_to_email:
        if emailer.send_inquiry(lot):
            set_email_status(lot["id"], "sent", email_sent_at=utcnow())
            sent += 1
        time.sleep(1)
    log.info("Отправлено запросов: %d/%d", sent, len(lots_to_email))


def main() -> None:
    parser = argparse.ArgumentParser(description="Парсинг торгов-россии.рф")
    parser.add_argument("--pages", type=int, default=None, help="кол-во страниц каталога")
    parser.add_argument("--x", type=int, default=None, help="последняя страница (при --pages это начало)")
    parser.add_argument("--no-details", action="store_true", help="не загружать карточки лотов")
    parser.add_argument("--workers", type=int, default=6, help="параллельность загрузки деталей")
    parser.add_argument("--batch", type=int, default=5, help="сохранять каждые N страниц")
    parser.add_argument("--send-email", action="store_true", help="после скрейпинга отправить запросы")
    parser.add_argument("--skip-photos", action="store_true", help="не скачивать фото лотов")
    args = parser.parse_args()

    if args.pages and args.x:
        start, end = args.pages, args.x
    elif args.pages:
        start, end = 1, args.pages
    else:
        start, end = 1, settings.torgi_max_pages

    fetch_details = not args.no_details
    t0 = time.time()
    pg = start
    total_found = total_new = 0
    while pg <= end:
        batch = min(args.batch, end - pg + 1)
        log.info("== страницы %d..%d ==", pg, pg + batch - 1)
        lots = scrape_torgi(
            max_pages=batch,
            fetch_details=fetch_details,
            workers=args.workers,
            start_page=pg,
        )
        found, new = store_torgi_lots(lots, download_pictures=not args.skip_photos)
        total_found += found
        total_new += new
        log.info(
            "== стр. %d..%d: найдено %d, новых %d; всего %d (новых %d), %.1f мин ==",
            pg,
            pg + batch - 1,
            found,
            new,
            total_found,
            total_new,
            (time.time() - t0) / 60,
        )
        pg += batch

    log.info("Итог: %d лотов (новых %d) за %.1f мин", total_found, total_new, (time.time() - t0) / 60)

    if args.send_email:
        send_inquiries()


if __name__ == "__main__":
    main()