"""Скрапер ЕФРСБ: поиск сообщений о продаже имущества (авто/техника) и извлечение данных.

Две стратегии:
  1. Live — работа с реальным сайтом через браузер Playwright.
  2. Offline — разбор сохранённых HTML-файлов сообщений из каталога (EFRSB_OFFLINE_DIR).
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from app import parser
from app.config import Settings
from app.db import upsert_lot
from app.scraper import selectors as sel
from app.scraper.session import BrowserSession, wait_page_load

log = logging.getLogger("efrsb.scraper")

# Типы сообщений ЕФРСБ, которые нам нужны
SALE_MESSAGE_TYPES = {
    "Сообщение о продаже имущества должника": "9",
    "Сообщение о продаже предприятия должника": "8",
    "О проведении торгов": "4",
}
VEHICLE_CATEGORIES = ["автотранспорт", "автомобили", "транспорт", "техника", "легковые автомобили"]


def find_locator(page: Page, candidates: list[str]):
    """Возвращает первый locator из кандидатов, который присутствует в DOM."""
    last = None
    for css in candidates:
        try:
            loc = page.locator(css).first
            if loc.count() > 0:
                return loc
            last = loc
        except Exception:  # noqa: BLE001
            continue
    return last


def click_by_text(page: Page, texts: list[str], tag: str = "Раскрыть элемент") -> bool:
    """Клик по элементу, найденному по тексту (label/button/ng-option)."""
    escaped = [re.escape(t) for t in texts]
    loc = page.get_by_text("|".join(escaped), exact=False).first
    try:
        if loc.count() == 0:
            return False
        loc.click(timeout=5000)
        log.debug("%s: клик по '%s'", tag, texts)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("%s: не удалось кликнуть по '%s': %s", tag, texts, e)
        return False


# ---------------------------------------------------------------------------
# Извлечение из текста сообщения
# ---------------------------------------------------------------------------
def _label_value(text: str, labels: list[str], strip_re: str | None = None) -> str | None:
    for lab in labels:
        pat = re.compile(
            "(?im)^[ \\t]*" + re.escape(lab) + r"\s*[:|\-]\s*(.{5,300}?)(?:\n\s*\n|\n(?=[\wА-ЯЁ\u0410-\u042f]{2,}[:))]))"
        )
        m = pat.search(text)
        if m:
            v = re.sub(r"\s+", " ", m.group(1)).strip()
            if strip_re:
                v = re.sub(strip_re, "", v).strip()
            return v
        # fallback: label в начале строки
        m2 = re.search(
            "(?im)[ \\t]*" + re.escape(lab) + r"\s*[:|\-]\s*(.{5,300})", text
        )
        if m2:
            v = re.sub(r"\s+", " ", m2.group(1)).strip()
            if "\n\n" in m2.group(1):
                v = m2.group(1).split("\n\n")[0].strip()
            if strip_re:
                v = re.sub(strip_re, "", v).strip()
            return v
    return None


def _first_line_containing(text: str, keywords: list[str], max_lines: int = 20) -> str | None:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines[: max_lines + 5]:
        low = line.lower()
        if any(k.lower() in low for k in keywords) and len(line) > 5:
            return line
    return None


def parse_message_text(raw_text: str, source_url: str = "", msg_id: str = "") -> dict:
    """Разбор текста сообщения ЕФРСБ в структуру лота."""
    text = parser.clean_text(raw_text)
    low = text.lower()

    from app.scraper.platforms import extract_platform_info

    platform_domain, platform_lot_url = extract_platform_info(text)

    lots = []
    # Разбиваем по "Лот №..."
    parts = re.split(r"(?i)(?m)^\s*лот(?:а)?\s*№?\s*(\d{1,4})\s*[:)\-]\s*", text)
    if not parts:
        parts = [text]

    def base_record() -> dict:
        return {
            "efrsb_id": msg_id or source_url,
            "source_url": source_url,
            "description": text,
            "vin": parser.best_vin(text),
            "category": parser.detect_category(text),
            "make_model": parser.detect_make_model(text),
            "price_amount": None,
            "sale_method": parser.detect_sale_method(text),
            "debtor": parser.normalize_fio(_label_value(text, ["Должник", "ФИО должника", "Должники"]) or ""),
            "debtor_inn": parser.clean_text(_label_value(text, ["ИНН должника", "ИНН"]) or ""),
            "trustee": parser.normalize_fio(
                _label_value(text, ["Арбитражный управляющий", "Конкурсный управляющий", "Финансовый управляющий"])
                or ""
            ),
            "organizer": parser.normalize_fio(_label_value(text, ["Организатор торгов"]) or ""),
            "publish_date": parser.parse_publication_date(
                _label_value(text, ["Дата публикации", "Дата опубликования", "Дата сообщения"]) or text
            ),
            "region": None,
            "trade_platform": platform_domain,
            "platform_lot_url": platform_lot_url,
            "raw": None,
        }

    chunks: list[str] = []
    if len(parts) > 1:
        for j in range(1, len(parts), 2):
            no = parts[j].strip() or str(j // 2 + 1)
            body = parts[j + 1] if j + 1 < len(parts) else ""
            chunks.append(f"ЛОТ {no}:\n{body}")
    else:
        chunks = [text]

    base = base_record()
    single = len(chunks) <= 1
    lot_n = 1
    for ch in chunks:
        rec = dict(base)
        rec["lot_no"] = str(lot_n)
        lot_n += 1
        # заголовок лота
        title = _first_line_containing(ch, ["наименование имущества", "предмет торгов", "сведения об имуществе"]) or ""
        if title:
            title = re.sub(r"^(?:наименование имущества|предмет торгов|сведения об имуществе)\s*[:)\-]\s*", "", title, flags=re.I).strip()
        else:
            tl = _first_line_containing(ch, ["автомобил", "машина", "авто", "грузов", "транспорт", "техника", "катер", "мотоцикл", "трактор", "экскаватор", "погрузчик"])
            title = tl if tl else _first_line_containing(ch, ["лот"], max_lines=30) or ""
            title = re.sub(r"^лот(?:а)?\s*\d*\s*[:)\-]*\s*", "", title, flags=re.I).strip()
        # обрезаем длинный заголовок по маркерам цены/длительности
        title = re.split(r"(?i)(?:начальная цена|цена лота|цена продажи|задаток|срок представления)", title)[0] if title else title
        rec["title"] = parser.clean_text(title[:300]) if title else (ch.strip().splitlines()[0][:300] if ch.strip() else "Имущество должника")
        # отбрасываем не-авто лоты (фильтрация по категории)
        cat = parser.detect_category(ch)
        rec["category"] = cat
        # цена
        price, cur = parser.detect_price(ch)
        rec["price_amount"], rec["price_currency"] = price, cur
        rec["description"] = parser.clean_text(ch) or text
        # VIN и марка — только из этого лота (наследовать базовые только для одиночного лота)
        vin = parser.best_vin(ch)
        rec["vin"] = vin if vin else (base.get("vin") if single else None)
        mm = parser.detect_make_model(ch, rec["vin"])
        if not mm and single:
            mm = base.get("make_model")
        rec["make_model"] = mm or ""
        rec["region"] = parser.detect_region(ch) or parser.detect_region(text)
        lots.append(rec)
    return {"base": base, "lots": lots}


# ---------------------------------------------------------------------------
# Разбор отдельных лотов из одного сообщения -> список лотов
# ---------------------------------------------------------------------------
def extract_lots(html: str, source_url: str = "", msg_id: str = "") -> list[dict]:
    text = parser.clean_text(html)
    parsed = parse_message_text(text, source_url, msg_id)
    return parsed["lots"]


# ---------------------------------------------------------------------------
# Скрапер (Live)
# ---------------------------------------------------------------------------
class EFRSBScraper:
    def __init__(self, settings: Settings, db_module=None):
        self.s = settings
        self.db = db_module
        self.stats = {"found": 0, "new": 0}

    def scrape_live(self, date_from: str = "", date_to: str = "") -> list[dict]:
        with BrowserSession(self.s) as bs:
            page = bs.page()
            bs.open_efrsb(page, url=self.s.search_url)
            wait_page_load(page, self.s)
            self._open_search_form(page)
            self._set_filters(page, date_from, date_to)
            if not self._submit_search(page):
                bs.dump_snapshot(page, "search_form")
                raise RuntimeError("Не удалось выполнить поиск на ЕФРСБ — см. снапшот в data/snapshots/")

            lots: list[dict] = []
            for page_idx in range(1, self.s.max_pages + 1):
                found = self._collect_rows_lots(page)
                lots.extend(found)
                self.stats["found"] += len(found)
                log.info("страница %s: собрано %s лотов (всего %s)", page_idx, len(found), self.stats["found"])
                if not self._go_next_page(page):
                    break
            return lots

    # -- шаги по UI ------------------------------------------------ #
    def _open_search_form(self, page: Page) -> None:
        # в новых версиях сайта расширенный поиск скрыт кнопкой
        click_by_text(page, ["Расширенный поиск", "Расширенный параметры", "Поиск сообщений"], "open_search")
        time.sleep(0.8)

    def _set_filters(self, page: Page, date_from: str, date_to: str) -> None:
        # тип сообщения: "Сообщение о продаже имущества должника"
        try:
            dd = find_locator(page, sel.MESSAGE_TYPE_DROPDOWN)
            if dd:
                dd.click(timeout=4000)
                time.sleep(0.5)
                click_by_text(page, ["Сообщение о продаже имущества должника", "продаже имущества должника"], "message_type")
        except Exception as e:  # noqa: BLE001
            log.debug("message type filter: %s", e)

        # категория: автотранспорт / автомобили
        try:
            dd = find_locator(page, sel.CATEGORY_DROPDOWN)
            if dd:
                dd.click(timeout=4000)
                time.sleep(0.5)
                click_by_text(page, ["Автотранспорт", "Автомобили и мототранспорт", "Транспортные средства", "Техника"], "category")
        except Exception as e:  # noqa: BLE001
            log.debug("category filter: %s", e)

        # даты
        for i, (cand, val) in enumerate([(sel.DATE_FROM, date_from), (sel.DATE_TO, date_to)]):
            if not val:
                continue
            loc = find_locator(page, cand)
            if loc:
                try:
                    loc.fill(val)
                except Exception as e:  # noqa: BLE001
                    log.debug("date fill %s: %s", i, e)

    def _submit_search(self, page: Page) -> bool:
        btn = find_locator(page, sel.SEARCH_BUTTON)
        if btn:
            try:
                btn.click(timeout=5000)
            except PlaywrightTimeoutError:
                return click_by_text(page, ["Найти", "Поиск"], "search_button")
            # дождёмся результатов
            for _ in range(30):
                try:
                    rows = page.locator(sel.RESULT_ROW[0]).count()
                except Exception:  # noqa: BLE001
                    rows = 0
                if rows:
                    return True
                time.sleep(1)
            return True
        return click_by_text(page, ["Найти", "Поиск"], "search_button")

    def _collect_rows_lots(self, page: Page) -> list[dict]:
        """Читает строки результата поиска и открывает каждый лот."""
        rows_sel = sel.RESULT_ROW
        loc = None
        for r in rows_sel:
            loc = page.locator(r)
            if loc.count():
                break
        if loc is None or loc.count() == 0:
            return []
        collected: list[dict] = []
        count = loc.count()
        for i in range(count):
            row = loc.nth(i)
            try:
                link = find_locator(row, sel.RESULT_LINK)
                href = link.get_attribute("href") if link and link.count() else None
                row_text = row.inner_text(timeout=5000)
            except Exception:  # noqa: BLE001
                continue
            msg_id = _msg_id_from_href(href)
            if msg_id:
                try:
                    detail = self._open_message(page, href or "", msg_id)
                except Exception as e:  # noqa: BLE001
                    log.warning("ошибка открытия лота %s: %s", msg_id, e)
                    continue
                lots = extract_lots(detail, source_url=urljoin(self.s.search_url, href or ""), msg_id=msg_id)
                for lot in lots:
                    if lot["vin"] or True:
                        collected.append(lot)
            else:
                # без ссылки — парсим строку таблицы
                lots = extract_lots(row_text, source_url=page.url, msg_id=msg_id or "")
                collected.extend(lots)
        return collected

    def _open_message(self, page: Page, href: str, msg_id: str) -> str:
        """Открывает карточку сообщения и возвращает её текст."""
        url = urljoin(self.s.search_url, href)
        try:
            page.goto(url, timeout=self.s.nav_timeout_ms, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            pass
        self._accept_redirect(page)
        # ждём контент
        for _ in range(15):
            try:
                body = page.locator("body").inner_text(timeout=2000)
                if len(body) > 300 and "403 Error" not in body:
                    return body
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.8)
        return page.locator("body").inner_text(timeout=5000)

    @staticmethod
    def _accept_redirect(page: Page) -> None:
        # лишние попапы/редиректы не трогаем
        time.sleep(0.3)

    @staticmethod
    def _go_next_page(page: Page) -> bool:
        nxt = find_locator(page, sel.PAGINATION_NEXT)
        if nxt:
            try:
                nxt.click(timeout=4000)
                time.sleep(1.5)
                return True
            except Exception:  # noqa: BLE001
                pass
        more = find_locator(page, sel.SHOW_MORE)
        if more:
            try:
                more.click(timeout=4000)
                time.sleep(1.5)
                return True
            except Exception:  # noqa: BLE001
                return False
        return False


def _msg_id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    try:
        qs = parse_qs(urlparse(href).query)
    except Exception:  # noqa: BLE001
        return None
    for key in ("id", "messageId", "msgId"):
        if key in qs and qs[key]:
            return qs[key][0]
    m = re.search(r"/(\d{4,})(?:/|$)", href)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Offline режим: разбор сохранённых HTML
# ---------------------------------------------------------------------------
def scrape_offline(dir_path: Path) -> list[dict]:
    lots: list[dict] = []
    for f in sorted(dir_path.glob("*.html")):
        html = f.read_text(encoding="utf-8", errors="replace")
        msg_id = f.stem
        lots.extend(extract_lots(html, source_url=str(f), msg_id=msg_id))
    return lots


def store_lots(lots: list[dict]) -> tuple[int, int]:
    new = 0
    for lot in lots:
        efrsb_id = lot.get("efrsb_id") or lot.get("source_url")
        if not efrsb_id:
            continue
        _, is_new = upsert_lot({k: v for k, v in lot.items() if k != "base"})
        if is_new:
            new += 1
    return len(lots), new
