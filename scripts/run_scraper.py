"""CLI-скрейпинг ЕФРСБ и поиск фото.

Примеры:
  python scripts/run_scraper.py --days 7
  python scripts/run_scraper.py --offline data/offline --find-photos
  python scripts/run_scraper.py --help
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db import init_db, start_run, finish_run
from app.scraper.efrsb import EFRSBScraper, scrape_offline, store_lots
from app.photos.search import fill_photos


def main() -> None:
    ap = argparse.ArgumentParser(description="ЕФРСБ агрегатор — скрейпинг")
    ap.add_argument("--days", type=int, default=None, help="глубина поиска в днях (по умолчанию EFRSB_DAYS_BACK)")
    ap.add_argument("--date-from", default="", help="YYYY-MM-DD")
    ap.add_argument("--date-to", default="", help="YYYY-MM-DD")
    ap.add_argument("--max-pages", type=int, default=None, help="максимум страниц результатов")
    ap.add_argument("--all-active", action="store_true", help="не ограничивать публикации датами")
    ap.add_argument("--offline", default="", help="каталог с сохранёнными HTML сообщений вместо живого сайта")
    ap.add_argument("--find-photos", action="store_true", help="искать фото по VIN (все лоты без фото)")
    ap.add_argument("--max-photos", type=int, default=200, help="максимум лотов для поиска фото")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    settings.ensure_dirs()
    init_db()

    rec = start_run()
    status, msg = "done", ""
    try:
        if args.all_active:
            args.date_from = ""
            args.date_to = ""
        elif args.date_from or args.date_to:
            pass
        elif args.days or settings.days_back:
            if args.days:
                settings.days_back = args.days
            args.date_to = datetime.now().strftime("%Y-%m-%d")
            args.date_from = (datetime.now() - timedelta(days=settings.days_back)).strftime("%Y-%m-%d")
        else:
            args.date_to = datetime.now().strftime("%Y-%m-%d")
            args.date_from = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        if args.offline:
            lots = scrape_offline(Path(args.offline))
        else:
            scraper = EFRSBScraper(settings)
            lots = scraper.scrape_live(date_from=args.date_from, date_to=args.date_to)
        found, new = store_lots(lots)
        msg = f"найдено лотов: {found}, новых: {new}"
        print(msg)

        photos = 0
        if args.find_photos:
            res = fill_photos(True, settings, max_photos_total=args.max_photos)
            photos = res.get("downloaded", 0)
            print(f"фото: обработано лотов {res.get('checked', 0)}, скачано фото {photos}")
            msg += f", фото: {photos}"

        finish_run(rec, status, msg, lots_found=found, lots_new=new, photos_found=photos)
    except Exception as e:  # noqa: BLE001
        logging.exception("скрейпинг не удался")
        finish_run(rec, "error", repr(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
