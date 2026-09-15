"""Автоматическая email-рассылка запросов управляющим и проверка ответов.

Фоновые циклы (демон-потоки) стартуют вместе с приложением:
- каждые EMAIL_SEND_INTERVAL_MIN — отправка запросов по лотам БЕЗ фото,
  с учётом дневного лимита EMAIL_SEND_DAILY_LIMIT;
- каждые EMAIL_CHECK_INTERVAL_MIN — проверка входящих по IMAP и обработка ответов.

Работают только если в .env заданы SMTP/IMAP настройки.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from app.config import Settings, settings
from app.db import (
    get_lots_for_email, set_email_status, utcnow, q,
    get_seen_message_ids, add_seen_message_ids, add_event,
    get_bounced_lots_for_email,
)
from app.emailer import get_email_service
from app.responder import process_email_response

log = logging.getLogger("efrsb.email_worker")

_workers_started = False
_worker_logged_unconfigured = False


# ---------------------------------------------------------------------------
# Логика отправки
# ---------------------------------------------------------------------------
def _count_sent_today() -> int:
    today = utcnow()[:10]
    r = q(
        "SELECT COUNT(*) c FROM lots WHERE email_sent_at LIKE ? AND email_status IN ('sent','responded')",
        (f"{today}%",),
    )
    return r[0]["c"] if r else 0


def daily_cap(s: Settings | None = None) -> int:
    """Дневной лимит с учётом прогрева ящика (растёт по дням)."""
    from datetime import date

    from app.db import get_meta, set_meta

    s = s or settings
    today = utcnow()[:10]
    start = get_meta("email_warmup_start")
    if not start:
        set_meta("email_warmup_start", today)
        start = today
    try:
        days = max(0, (date.fromisoformat(today) - date.fromisoformat(start[:10])).days)
    except ValueError:
        days = 0
    cap = s.email_warmup_start + s.email_warmup_step * days
    return max(s.email_warmup_start, min(cap, s.email_send_daily_limit))


def warmup_day() -> int:
    from datetime import date

    from app.db import get_meta

    start = get_meta("email_warmup_start")
    if not start:
        return 0
    try:
        return max(0, (date.fromisoformat(utcnow()[:10]) - date.fromisoformat(start[:10])).days)
    except ValueError:
        return 0


def _lots_for_reminder(s: Settings, follow_up_days: int) -> list[dict]:
    """Лоты, которым запрос был отправлен и не получен ответ (напоминание, только активная продажа)."""
    from datetime import timedelta, timezone, datetime

    from app.db import ACTIVE_SALE_SQL

    cutoff = (datetime.now(timezone.utc) - timedelta(days=follow_up_days)).isoformat(timespec="seconds")
    return q(
        f"""SELECT lots.* FROM lots
           WHERE lots.email_status='sent'
             AND lots.email_sent_at < ?
             AND lots.email_response_at IS NULL
             AND NOT EXISTS (SELECT 1 FROM photos WHERE photos.lot_id=lots.id AND photos.status='ok')
             AND lots.contact_email IS NOT NULL AND lots.contact_email != ''
             AND {ACTIVE_SALE_SQL}
           ORDER BY lots.email_sent_at ASC""",
        (cutoff,),
    )


def do_send_emails(s: Settings | None = None) -> dict:
    """Отправить запросы по лотам без фото (новые) + напоминания. Возвращает статистику."""
    s = s or settings
    emailer = get_email_service()
    if not emailer.is_configured():
        global _worker_logged_unconfigured
        if not _worker_logged_unconfigured:
            log.warning("email-рассылка: SMTP не сконфигурирован — автоотправка выключена")
            _worker_logged_unconfigured = True
        return {"sent": 0, "skipped": "smtp_not_configured"}

    # Лимит: сколько ещё можно отправить сегодня (с учётом прогрева)
    cap = daily_cap(s)
    remaining = cap - _count_sent_today()
    if remaining <= 0:
        log.info("email-рассылка: дневной лимит %d (день прогрева %d) исчерпан", cap, warmup_day())
        return {"sent": 0, "limit_reached": True, "daily_cap": cap}

    # Основная очередь — лоты без фото
    lots = get_lots_for_email(limit=s.email_batch_size, no_photos_only=True)

    # Напоминания для отправленных давно и без ответа
    if s.email_follow_up_days and remaining - len(lots) > 0:
        for lot in _lots_for_reminder(s, s.email_follow_up_days):
            if len(lots) >= remaining:
                break
            # не дублируем напоминание для лота из основной очереди
            if not any(l.get("id") == lot["id"] for l in lots):
                lot = dict(lot)
                lot["_reminder"] = True
                lots.append(lot)

    lot_clean = [l for l in lots if l.get("contact_email")][:remaining]
    sent = errors = 0
    for lot in lot_clean:
        try:
            if emailer.send_inquiry(lot):
                set_email_status(lot["id"], "sent", email_sent_at=utcnow(), email_sender="primary")
                add_event(
                    "email_sent",
                    f"Отправлен запрос по лоту №{lot.get('lot_no')} на {lot.get('contact_email')}",
                    lot["id"],
                )
                sent += 1
            else:
                errors += 1
        except Exception as e:  # noqa: BLE001
            log.error("email лот %s: %s", lot.get("lot_no"), e)
            errors += 1
        time.sleep(max(3, s.email_send_delay_sec))

    # Повторная отправка недоставленных — со второй почты
    resent = _retry_bounced(s)

    log.info(
        "email-рассылка: отправлено %d, повторно %d, ошибок %d, очередь %d, лимит дня %d (день прогрева %d)",
        sent, resent, errors, len(lot_clean), cap, warmup_day(),
    )
    return {"sent": sent, "resent": resent, "errors": errors, "queued": len(lot_clean), "daily_cap": cap}


def _retry_bounced(s: Settings) -> int:
    """Повторно отправить недоставленные письма со второй почты (если включена)."""
    if not s.email2_enabled:
        return 0
    sec = get_email_service("secondary")
    if not sec.is_configured():
        return 0

    cap = daily_cap(s)
    remaining = cap - _count_sent_today()
    if remaining <= 0:
        return 0

    lots = get_bounced_lots_for_email(limit=min(s.email_batch_size, remaining))
    resent = 0
    for lot in lots:
        try:
            if sec.send_inquiry(lot):
                set_email_status(lot["id"], "sent", email_sent_at=utcnow(), email_sender="secondary")
                add_event(
                    "email_resent",
                    f"Повторно со второй почты по лоту №{lot.get('lot_no')} на {lot.get('contact_email')}",
                    lot["id"],
                )
                resent += 1
            else:
                # не удалось отправить — чтобы не зацикливаться, помечаем попытку
                set_email_status(lot["id"], "bounced", email_sender="secondary")
        except Exception as e:  # noqa: BLE001
            log.error("повторная отправка лот %s: %s", lot.get("lot_no"), e)
            set_email_status(lot["id"], "bounced", email_sender="secondary")
        time.sleep(max(3, s.email_send_delay_sec))

    if resent:
        log.info("email-рассылка: повторно отправлено со второй почты: %d", resent)
    return resent


# ---------------------------------------------------------------------------
# Логика проверки ответов
# ---------------------------------------------------------------------------
def _mark_bounced(addrs: list[str]) -> int:
    """Пометить лоты статусом 'bounced' по адресам из отчётов о недоставке."""
    if not addrs:
        return 0
    ph = ",".join("?" * len(addrs))
    rows = q(
        f"SELECT id FROM lots WHERE lower(contact_email) IN ({ph})",
        [a.lower() for a in addrs],
    )
    for r in rows:
        set_email_status(r["id"], "bounced")
    if rows:
        add_event("bounce", f"Письмо не доставлено: {', '.join(addrs)}", rows[0]["id"])
        log.info("email-проверка: помечено недоставленных лотов: %d (%s)", len(rows), ", ".join(addrs))
    return len(rows)


def do_check_emails(s: Settings | None = None) -> dict:
    """Проверить входящие по IMAP (оба ящика) и сохранить ответы (состояние + фото)."""
    s = s or settings
    emailer = get_email_service("primary")

    services = [emailer]
    if s.email2_enabled:
        sec = get_email_service("secondary")
        if sec.is_imap_configured():
            services.append(sec)

    if not any(sv.is_imap_configured() for sv in services):
        return {"processed": 0, "skipped": "imap_not_configured"}

    own_addresses = [sv.from_email for sv in services if sv.from_email]

    responses: list[dict] = []
    for sv in services:
        if sv.is_imap_configured():
            responses.extend(sv.check_responses(since_hours=max(1, s.email_check_days) * 24))
    if not responses:
        return {"responses": 0, "processed": 0}

    sent_lots = q(
        """SELECT * FROM lots
           WHERE email_status IN ('sent','responded')
           AND contact_email IS NOT NULL AND contact_email != ''"""
    )
    processed = 0
    skipped = 0
    bounced = 0
    seen = get_seen_message_ids()
    newly_seen: list[str] = []
    for resp in responses:
        # отчёт о недоставке — помечаем лоты как bounced
        bounce_addrs = emailer.extract_bounce_recipients(resp)
        if bounce_addrs:
            bounced += _mark_bounced(bounce_addrs)
            skipped += 1
            continue
        mid = resp.get("message_id") or ""
        if mid and mid in seen:
            continue
        if not emailer.is_valid_response(resp, own_addresses=own_addresses):
            skipped += 1
            continue
        lot_id = emailer.match_response_to_lot(resp, sent_lots)
        if not lot_id:
            continue
        try:
            result = process_email_response(lot_id, resp, s.data_dir)
            processed += 1
            if mid:
                newly_seen.append(mid)
            lot_no = next((l.get("lot_no") for l in sent_lots if l.get("id") == lot_id), lot_id)
            n_photos = result.get("photos_saved", 0)
            cond = result.get("condition", {})
            extra = f", состояние: {cond.get('condition_summary', '')[:80]}" if cond.get("condition_summary") else ""
            add_event(
                "response",
                f"Получен ответ по лоту №{lot_no}: фото {n_photos}{extra}",
                lot_id,
            )
            log.info("ответ по лоту %d обработан: %d фото", lot_id, result.get("photos_saved", 0))
        except Exception as e:  # noqa: BLE001
            log.error("ошибка обработки ответа по лоту %d: %s", lot_id, e)

    if newly_seen:
        add_seen_message_ids(newly_seen)
    log.info("email-проверка: ответов %d, обработано %d, отсеяно %d, недоставлено %d",
             len(responses), processed, skipped, bounced)
    return {"responses": len(responses), "processed": processed, "skipped": skipped, "bounced": bounced}


# ---------------------------------------------------------------------------
# Демон-потоки
# ---------------------------------------------------------------------------
def _send_loop() -> None:
    delay = max(0, settings.email_auto_send_delay_min) * 60
    if delay:
        log.info("email-рассылка: первый запуск через %d мин", int(delay // 60))
        time.sleep(delay)
    while True:
        try:
            do_send_emails(settings)
        except Exception:  # noqa: BLE001
            log.exception("email-рассылка: сбой цикла")
        time.sleep(max(1, settings.email_send_interval_min) * 60)


def _check_loop() -> None:
    while True:
        try:
            do_check_emails(settings)
        except Exception:  # noqa: BLE001
            log.exception("email-проверка: сбой цикла")
        time.sleep(max(1, settings.email_check_interval_min) * 60)


def start_email_workers() -> None:
    """Запустить фоновую авто-рассылку и проверку ответов (идемпотентно)."""
    global _workers_started
    if _workers_started:
        return
    _workers_started = True

    if settings.email_auto_send:
        threading.Thread(target=_send_loop, daemon=True, name="email-send-worker").start()
    else:
        log.info("email-рассылка: автоотправка отключена (EMAIL_AUTO_SEND=0)")

    if settings.email_auto_check:
        threading.Thread(target=_check_loop, daemon=True, name="email-check-worker").start()
    else:
        log.info("email-проверка: автоотправка отключена (EMAIL_AUTO_CHECK=0)")


# Экспорт для ручного запуска из API/CLI
def run_send_now(s: Settings | None = None) -> dict:
    return do_send_emails(s)


def run_check_now(s: Settings | None = None) -> dict:
    return do_check_emails(s)