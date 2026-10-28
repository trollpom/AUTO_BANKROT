"""Email-сервис: автоматическая отправка запросов управляющим и приём ответов.

Отправка через SMTP (с отправкой с почты пользователя).
Приём ответов через IMAP (проверка входящих).
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import smtplib
import time
import uuid
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.header import decode_header
from email.utils import formataddr, formatdate
from pathlib import Path
from typing import Optional

log = logging.getLogger("efrsb.emailer")


def _is_placeholder(value: str) -> bool:
    """Отсеять незаполненные заготовки из .env (ВПИШИТЕ..., your_...)."""
    v = (value or "").strip().lower()
    if not v:
        return True
    markers = ("впишите", "your_", "your-", "example", "changeme", "заполните", "<", ">")
    return any(m in v for m in markers)


class EmailService:
    """Сервис для отправки и приёма email."""

    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        imap_host: str = "",
        imap_port: int = 993,
        imap_user: str = "",
        imap_password: str = "",
        from_name: str = "car_lot_pilot",
        from_email: str = "",
        use_tls: bool = True,
        subject: str = "Запрос доп. информации по имуществу",
        signature: str = "Евгений Смирнов",
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.imap_user = imap_user or smtp_user
        self.imap_password = imap_password or smtp_password
        self.from_name = from_name
        self.from_email = from_email or smtp_user
        self.use_tls = use_tls
        self.subject = subject
        self.signature = signature

    @classmethod
    def from_config(cls, which: str = "primary") -> EmailService:
        """Создать из настроек приложения (загружают .env)."""
        from app.config import settings

        if which == "secondary":
            return cls(
                smtp_host=settings.email2_smtp_host,
                smtp_port=settings.email2_smtp_port,
                smtp_user=settings.email2_smtp_user,
                smtp_password=settings.email2_smtp_password,
                imap_host=settings.email2_imap_host,
                imap_port=settings.email2_imap_port,
                imap_user=settings.email2_imap_user,
                imap_password=settings.email2_imap_password,
                from_name=settings.email2_from_name,
                from_email=settings.email2_from,
                use_tls=settings.email2_use_tls,
                subject=settings.email_subject,
                signature=settings.email_signature,
            )
        return cls(
            smtp_host=settings.email_smtp_host,
            smtp_port=settings.email_smtp_port,
            smtp_user=settings.email_smtp_user,
            smtp_password=settings.email_smtp_password,
            imap_host=settings.email_imap_host,
            imap_port=settings.email_imap_port,
            imap_user=settings.email_imap_user,
            imap_password=settings.email_imap_password,
            from_name=settings.email_from_name,
            from_email=settings.email_from,
            use_tls=settings.email_use_tls,
            subject=settings.email_subject,
            signature=settings.email_signature,
        )

    def is_configured(self) -> bool:
        return bool(
            self.smtp_host
            and not _is_placeholder(self.smtp_user)
            and not _is_placeholder(self.smtp_password)
        )

    def is_imap_configured(self) -> bool:
        return bool(
            self.imap_host
            and not _is_placeholder(self.imap_user)
            and not _is_placeholder(self.imap_password)
        )

    # ------------------------------------------------------------------
    # Шаблон письма
    # ------------------------------------------------------------------
    _YEAR_RE = re.compile(r"(?:год изготовления[:\s]*)?\b((?:19|20)\d{2})\s*г?\.?\s*в?\.?", re.IGNORECASE)
    _VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
    _MAKE_MODEL_RES = (
        re.compile(r"марка[:\s]+([^,;]+?)[,;]\s*модель[:\s]+([^,;.]+)", re.IGNORECASE),
        re.compile(r"марки\s+([^\s,]+)\s+модель\s+([^\s,.]+)", re.IGNORECASE),
        re.compile(r"автомобиль\s+([A-Za-zА-Яа-я0-9\-]+)\s+([A-Za-zА-Яа-я0-9\-]+)", re.IGNORECASE),
    )

    @classmethod
    def _extract_year(cls, lot: dict) -> str:
        for src in (lot.get("title") or "", lot.get("description") or ""):
            m = cls._YEAR_RE.search(src)
            if m:
                return m.group(1)
        return ""

    @classmethod
    def _extract_vin(cls, lot: dict) -> str:
        vin = (lot.get("vin") or "").strip()
        if vin:
            return vin
        for src in (lot.get("title") or "", lot.get("description") or ""):
            m = cls._VIN_RE.search((src or "").upper())
            if m:
                return m.group(1)
        return ""

    @classmethod
    def _extract_make_model(cls, lot: dict) -> str:
        text = lot.get("title") or ""
        for rx in cls._MAKE_MODEL_RES:
            m = rx.search(text)
            if m:
                a, b = m.group(1).strip(), m.group(2).strip()
                if a and b:
                    return f"{a} {b}"
        mm = (lot.get("make_model") or "").strip()
        if mm and "модель" not in mm.lower():
            return mm
        return mm or "не указан"

    def _car_line(self, lot: dict) -> str:
        desc = self._extract_make_model(lot)
        year = self._extract_year(lot)
        vin = self._extract_vin(lot) or "не указан"
        debtor = (lot.get("debtor") or "").strip() or "не указан"
        year_part = f", {year} года выпуска" if year else ""
        return (
            f"Интересующее имущество автомобиль {desc}{year_part}, "
            f"VIN: {vin}. Должник - {debtor}"
        )

    def build_inquiry_body(self, lot: dict) -> str:
        """Сформировать текст запроса по шаблону."""
        return (
            f"Добрый день!\n\n"
            f"{self._car_line(lot)}\n\n"
            f"Подскажите возможно получить доп. информацию о тех состоянии авто и  фото? \n"
            f"За ранее спасибо!\n\n"
            f"С Уважением,\n"
            f"{self.signature}"
        )

    def build_inquiry_subject(self, lot: dict) -> str:
        """Тема письма."""
        return self.subject

    # ------------------------------------------------------------------
    # Отправка
    # ------------------------------------------------------------------
    def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes]] | None = None,
        lot_id: int | None = None,
    ) -> bool:
        """Отправить email. Возвращает True при успехе."""
        if not self.is_configured():
            log.error("Email не сконфигурирован (нет SMTP настроек)")
            return False

        msg = MIMEMultipart()
        msg["From"] = formataddr((self.from_name, self.from_email))
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        # Reply-To — на наш адрес
        if self.from_email:
            msg["Reply-To"] = self.from_email
        # Message-ID с номером лота — по нему сопоставляем ответы (In-Reply-To/References).
        # Домен берём реальный (от адреса отправителя), иначе спам-фильтры режут письмо.
        if lot_id is not None:
            domain = self.from_email.split("@")[-1] if "@" in self.from_email else "yandex.ru"
            msg["Message-ID"] = f"<lot-{lot_id}.{uuid.uuid4().hex[:12]}@{domain}>"
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Вложения
        if attachments:
            for filename, data in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(data)
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition", f'attachment; filename="{filename}"'
                )
                msg.attach(part)

        try:
            if self.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
            with server:
                if self.smtp_port != 465 and self.use_tls:
                    server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg, from_addr=self.from_email)
            log.info("email отправлен: %s -> %s", self.from_email, to_email)
            return True
        except Exception as e:
            log.error("ошибка отправки email: %s", e)
            return False

    def send_inquiry(self, lot: dict) -> bool:
        """Отправить шаблонный запрос по лоту."""
        to_email = lot.get("contact_email")
        if not to_email:
            log.warning("нет email для лота %s", lot.get("lot_no"))
            return False

        subject = self.build_inquiry_subject(lot)
        body = self.build_inquiry_body(lot)
        return self.send_email(to_email, subject, body, lot_id=lot.get("id"))

    def send_batch_inquiries(self, lots: list[dict]) -> tuple[int, int]:
        """Отправить запросы по списку лотов. Возвращает (успешно, ошибки)."""
        sent = 0
        errors = 0
        for lot in lots:
            if not lot.get("contact_email"):
                continue
            try:
                if self.send_inquiry(lot):
                    sent += 1
                else:
                    errors += 1
            except Exception as e:
                log.error("ошибка отправки лот %s: %s", lot.get("lot_no"), e)
                errors += 1
            time.sleep(1)  # пауза между отправками
        return sent, errors

    # ------------------------------------------------------------------
    # Приём ответов через IMAP
    # ------------------------------------------------------------------
    def check_responses(self, since_hours: int = 24) -> list[dict]:
        """Проверить входящие письма на ответы по лотам.

        Возвращает список словарей с:
        - from_email
        - subject
        - body (текст)
        - date
        - attachments (список имён файлов)
        """
        if not self.is_imap_configured():
            log.error("IMAP не сконфигурирован")
            return []

        responses = []
        try:
            mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            mail.login(self.imap_user, self.imap_password)
            mail.select("INBOX")

            # Поиск писем за последние N часов (IMAP SINCE принимает только дату)
            since_dt = datetime.now(timezone.utc) - timedelta(hours=max(0, since_hours))
            since_date = since_dt.strftime("%d-%b-%Y")
            status, messages = mail.search(None, f'(SINCE "{since_date}")')

            if status != "OK":
                return []

            msg_ids = messages[0].split()
            for msg_id in msg_ids[-500:]:  # последние 500
                status, data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue

                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = self._decode_header(msg.get("Subject", ""))
                from_email = self._decode_header(msg.get("From", ""))
                date_str = msg.get("Date", "")

                body = ""
                delivery_status = ""
                attachments_names = []
                attachments_raw: list[tuple[str, bytes]] = []

                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        disposition = str(part.get("Content-Disposition", ""))

                        if "attachment" in disposition:
                            filename = part.get_filename()
                            if filename:
                                filename = self._decode_header(filename)
                            payload = part.get_payload(decode=True)
                            if payload:
                                attachments_raw.append((filename or "attachment", payload))
                            if filename:
                                attachments_names.append(filename)
                        elif content_type == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode("utf-8", errors="replace")
                        elif content_type in ("message/delivery-status", "text/rfc822-headers"):
                            # машинная часть отчёта о недоставке (Final-Recipient и пр.)
                            payload = part.get_payload(decode=True)
                            if payload:
                                delivery_status += payload.decode("utf-8", errors="replace")
                            else:
                                try:
                                    for sub in part.get_payload():
                                        delivery_status += str(sub) + "\n"
                                except Exception:  # noqa: BLE001
                                    pass
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode("utf-8", errors="replace")

                responses.append({
                    "from_email": from_email,
                    "subject": subject,
                    "body": body,
                    "delivery_status": delivery_status,
                    "date": date_str,
                    "attachments": attachments_names,
                    "attachments_raw": attachments_raw,
                    "message_id": msg.get("Message-ID", ""),
                    "in_reply_to": msg.get("In-Reply-To", ""),
                    "references": msg.get("References", ""),
                    "auto_submitted": msg.get("Auto-Submitted", ""),
                    "precedence": msg.get("Precedence", ""),
                    "return_path": msg.get("Return-Path", ""),
                })

            mail.logout()
        except Exception as e:
            log.error("ошибка IMAP: %s", e)

        log.info("проверено входящих: %d ответов", len(responses))
        return responses

    def extract_bounce_recipients(self, response: dict) -> list[str]:
        """Если письмо — отчёт о недоставке, вернуть адреса, которым не доставили."""
        frm = (response.get("from_email") or "").lower()
        subj = (response.get("subject") or "").lower()
        body = response.get("body") or ""
        auto = (response.get("auto_submitted") or "").lower()

        is_bounce = (
            "mailer-daemon" in frm
            or "postmaster" in frm
            or auto == "auto-replied"
            or "недоставлен" in subj
            or "undeliver" in subj
            or "delivery failure" in subj
            or "delivery status" in subj
            or "failure notice" in subj
            or "returned mail" in subj
        )
        if not is_bounce:
            return []

        haystack = (response.get("delivery_status") or "") + "\n" + body
        addrs: set[str] = set()
        for m in re.finditer(
            r"(?:Final-Recipient|Original-Recipient):\s*rfc822;\s*([\w.+-]+@[\w.-]+)",
            haystack, re.IGNORECASE,
        ):
            addrs.add(m.group(1).lower())
        if not addrs:
            for m in re.finditer(r"<([\w.+-]+@[\w.-]+)>", haystack):
                addrs.add(m.group(1).lower())
        return list(addrs)

    def is_valid_response(self, response: dict, own_addresses: list[str] | None = None) -> bool:
        """Отсеять свои письма, bounce-сообщения и автоответы."""
        frm = (response.get("from_email") or "").lower()
        subj = (response.get("subject") or "").lower()
        auto = (response.get("auto_submitted") or "").lower()
        prec = (response.get("precedence") or "").lower()

        # своё собственное письмо (любой из наших адресов)
        own = own_addresses if own_addresses is not None else ([self.from_email] if self.from_email else [])
        if any(o and o.lower() in frm for o in own):
            return False
        # автоответ / системное
        if auto and auto != "no":
            return False
        if prec in ("bulk", "junk", "auto_reply", "auto-reply"):
            return False
        # bounce / почтовые роботы
        bad_senders = (
            "mailer-daemon", "postmaster", "no-reply", "noreply",
            "donotreply", "do-not-reply", "mail-daemon", "mailerdaemon",
        )
        if any(b in frm for b in bad_senders):
            return False
        bad_subj = (
            "undeliverable", "delivery status", "delivery failure", "returned mail",
            "mail delivery", "failure notice", "delivery has failed", "delivery report",
            "автоответ", "авто-ответ", "автоматический ответ", "недоставлен", "не доставлено",
            "out of office", "automatic reply", "auto reply", "vacation",
        )
        if any(b in subj for b in bad_subj):
            return False
        return True

    def match_response_to_lot(self, response: dict, lots: list[dict]) -> int | None:
        """Сопоставить ответ с лотом.

        Сначала — по Message-ID (In-Reply-To/References содержат <lot-ID@...>),
        затем по номеру лота или VIN в теме/теле.
        """
        # 0) Самый надёжный признак: ответ пришёл с email управляющего по лоту
        sender = (response.get("from_email") or "").lower()
        m_sender = re.search(r"[\w.+-]+@[\w.-]+", sender)
        sender_addr = m_sender.group(0) if m_sender else sender
        if sender_addr:
            for lot in lots:
                ce = (lot.get("contact_email") or "").lower().strip()
                if ce and ce == sender_addr:
                    return lot["id"]

        # 1) Надёжно: по заголовкам ответа (наш Message-ID <lot-<id>@...>)
        refs = " ".join(
            str(response.get(k) or "") for k in ("in_reply_to", "references", "message_id")
        )
        m = re.search(r"lot-(\d+)[.@]", refs)
        if m:
            rid = int(m.group(1))
            if any(l.get("id") == rid for l in lots):
                return rid
            return rid

        # 2) По номеру лота / VIN в теме и теле
        subject = (response.get("subject") or "").lower()
        body = (response.get("body") or "").lower()

        for lot in lots:
            lot_no = str(lot.get("lot_no", ""))
            vin = (lot.get("vin") or "").lower()

            if lot_no and (f"лот №{lot_no}" in subject or f"лот {lot_no}" in subject):
                return lot["id"]

            if vin and vin in subject:
                return lot["id"]
            if vin and vin in body:
                return lot["id"]

        return None

    @staticmethod
    def _decode_header(header: str) -> str:
        """Декодировать заголовок письма."""
        if not header:
            return ""
        decoded_parts = decode_header(header)
        result = []
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(encoding or "utf-8", errors="replace"))
            else:
                result.append(str(part))
        return "".join(result)


# Единые экземпляры (primary / secondary)
_email_services: dict[str, EmailService] = {}


def get_email_service(which: str = "primary") -> EmailService:
    if which not in _email_services:
        _email_services[which] = EmailService.from_config(which)
    return _email_services[which]
