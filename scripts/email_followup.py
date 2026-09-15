"""CLI: email-запросы управляющим и приём ответов.

Примеры:
  python scripts/email_followup.py --send          # отправить запросы
  python scripts/email_followup.py --check         # проверить и обработать ответы
  python scripts/email_followup.py --send --check  # оба шага
"""
from __future__ import annotations

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def send_inquiries() -> int:
    from app.emailer import get_email_service
    from app.db import get_lots_for_email, set_email_status, utcnow

    emailer = get_email_service()
    if not emailer.is_configured():
        print("Email не сконфигурирован. Заполните EMAIL_SMTP_* в .env")
        return 0

    lots = get_lots_for_email(limit=500)
    sent = errors = 0
    for lot in lots:
        if not lot.get("contact_email"):
            continue
        try:
            if emailer.send_inquiry(lot):
                set_email_status(lot["id"], "sent", email_sent_at=utcnow())
                sent += 1
            else:
                errors += 1
        except Exception as e:  # noqa: BLE001
            logging.warning("лот %s: %s", lot.get("lot_no"), e)
            errors += 1
        import time
        time.sleep(1)

    print(f"Отправлено: {sent}, ошибок: {errors}, всего кандидатов: {len(lots)}")
    return sent


def check_responses() -> int:
    from app.emailer import get_email_service
    from app.responder import process_email_response
    from app.config import settings
    from app.db import q

    emailer = get_email_service()
    if not emailer.is_imap_configured():
        print("IMAP не сконфигурирован. Заполните EMAIL_IMAP_* в .env")
        return 0

    responses = emailer.check_responses(since_hours=72)
    lots = q(
        """SELECT * FROM lots
           WHERE email_status IN ('sent','responded')
           AND contact_email IS NOT NULL AND contact_email != ''"""
    )
    processed = 0
    for resp in responses:
        lot_id = emailer.match_response_to_lot(resp, lots)
        if not lot_id:
            continue
        result = process_email_response(lot_id, resp, settings.data_dir)
        processed += 1
        print(f"Ответ обработан: лот {lot_id}, фото: {result['photos_saved']}")

    print(f"Найдено ответов: {len(responses)}, обработано: {processed}")
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Email-запросы управляющим")
    parser.add_argument("--send", action="store_true", help="отправить запросы")
    parser.add_argument("--check", action="store_true", help="проверить ответы")
    args = parser.parse_args()

    if not args.send and not args.check:
        parser.print_help()
        return

    if args.send:
        send_inquiries()
    if args.check:
        check_responses()


if __name__ == "__main__":
    main()