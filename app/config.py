"""Настройки сервиса. Все параметры можно переопределить через переменные окружения."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env из корня проекта (существующие переменные env имеют приоритет)
BASE_DIR_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR_DIR / ".env", override=False)

BASE_DIR = Path(os.getenv("EFRSB_BASE_DIR", BASE_DIR_DIR))
DATA_DIR = Path(os.getenv("EFRSB_DATA", BASE_DIR / "data"))
PHOTOS_DIR = DATA_DIR / "photos"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DB_PATH = Path(os.getenv("EFRSB_DB", DATA_DIR / "efrsb.db"))


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    def __init__(self) -> None:
        self.base_dir = BASE_DIR
        self.data_dir = DATA_DIR
        self.photos_dir = PHOTOS_DIR
        self.snapshot_dir = SNAPSHOT_DIR
        self.db_path = DB_PATH

        # Браузер и сеть
        self.headless: bool = _env_bool("EFRSB_HEADLESS", True)
        self.browser_channel: str = os.getenv("EFRSB_BROWSER", "msedge")  # 'msedge'|'chrome'|'chromium'
        self.proxy: str | None = os.getenv("EFRSB_PROXY") or None          # http://user:pass@host:port :80
        self.proxies: dict[str, str] = {"http": self.proxy, "https": self.proxy} if self.proxy else {}
        self.user_agent: str = os.getenv(
            "EFRSB_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        )
        self.timeout_ms: int = _env_int("EFRSB_TIMEOUT", 90000)
        self.nav_timeout_ms: int = _env_int("EFRSB_NAV_TIMEOUT", 60000)
        self.locale: str = os.getenv("EFRSB_LOCALE", "ru-RU")
        self.search_url: str = os.getenv(
            "EFRSB_SEARCH_URL", "https://fedresurs.ru/sfactmessages/"
        )

        # Поиск
        self.max_pages: int = _env_int("EFRSB_MAX_PAGES", 200)
        self.days_back: int = _env_int("EFRSB_DAYS_BACK", 30)
        self.message_types: list[str] = None or []          # заполняются в scraper.efrsb
        self.categories: list[str] | None = None             # None = все авто/техника

        # Фото
        self.photo_engines: list[str] = [
            e.strip()
            for e in os.getenv("EFRSB_PHOTO_ENGINES", "yandex,bing,duckduckgo").split(",")
            if e.strip()
        ]
        self.photos_per_lot: int = _env_int("EFRSB_PHOTOS_PER_LOT", 24)
        self.photo_min_width: int = _env_int("EFRSB_PHOTO_MIN_WIDTH", 120)
        self.photo_min_height: int = _env_int("EFRSB_PHOTO_MIN_HEIGHT", 80)
        self.download_concurrency: int = _env_int("EFRSB_DOWNLOAD_CONCURRENCY", 4)
        self.referer_policy: str = os.getenv("EFRSB_REFERER_POLICY", "engine")  # engine|site|none

        # API / UI
        self.host: str = os.getenv("EFRSB_HOST", "127.0.0.1")
        self.port: int = _env_int("EFRSB_PORT", 8000)
        self.verbose: bool = _env_bool("EFRSB_VERBOSE", True)
        # Пароль администратора (расширенные функции и журнал)
        self.admin_password: str = os.getenv("ADMIN_PASSWORD", "")

        # Email
        self.email_smtp_host: str = os.getenv("EMAIL_SMTP_HOST", "")
        self.email_smtp_port: int = _env_int("EMAIL_SMTP_PORT", 587)
        self.email_smtp_user: str = os.getenv("EMAIL_SMTP_USER", "")
        self.email_smtp_password: str = os.getenv("EMAIL_SMTP_PASSWORD", "")
        self.email_imap_host: str = os.getenv("EMAIL_IMAP_HOST", "")
        self.email_imap_port: int = _env_int("EMAIL_IMAP_PORT", 993)
        self.email_imap_user: str = os.getenv("EMAIL_IMAP_USER", "")
        self.email_imap_password: str = os.getenv("EMAIL_IMAP_PASSWORD", "")
        self.email_from_name: str = os.getenv("EMAIL_FROM_NAME", "Автобанкрот")
        self.email_from: str = os.getenv("EMAIL_FROM", "")
        self.email_use_tls: bool = _env_bool("EMAIL_USE_TLS", True)
        # Вторая почта — для повторной отправки недоставленных писем
        self.email2_enabled: bool = _env_bool("EMAIL2_ENABLED", False)
        self.email2_smtp_host: str = os.getenv("EMAIL2_SMTP_HOST", "")
        self.email2_smtp_port: int = _env_int("EMAIL2_SMTP_PORT", 465)
        self.email2_smtp_user: str = os.getenv("EMAIL2_SMTP_USER", "")
        self.email2_smtp_password: str = os.getenv("EMAIL2_SMTP_PASSWORD", "")
        self.email2_imap_host: str = os.getenv("EMAIL2_IMAP_HOST", "")
        self.email2_imap_port: int = _env_int("EMAIL2_IMAP_PORT", 993)
        self.email2_imap_user: str = os.getenv("EMAIL2_IMAP_USER", "")
        self.email2_imap_password: str = os.getenv("EMAIL2_IMAP_PASSWORD", "")
        self.email2_from_name: str = os.getenv("EMAIL2_FROM_NAME", os.getenv("EMAIL_FROM_NAME", "Автобанкрот"))
        self.email2_from: str = os.getenv("EMAIL2_FROM", "")
        self.email2_use_tls: bool = _env_bool("EMAIL2_USE_TLS", True)
        # Шаблон письма
        self.email_subject: str = os.getenv("EMAIL_SUBJECT", "Запрос доп. информации по имуществу")
        self.email_signature: str = os.getenv("EMAIL_SIGNATURE", "")
        # Авто-рассылка запросов управляющим (запускается при старте приложения)
        self.email_auto_send: bool = _env_bool("EMAIL_AUTO_SEND", False)
        self.email_auto_send_delay_min: int = _env_int("EMAIL_AUTO_SEND_DELAY_MIN", 2)
        self.email_send_interval_min: int = _env_int("EMAIL_SEND_INTERVAL_MIN", 60)
        self.email_batch_size: int = _env_int("EMAIL_BATCH_SIZE", 20)
        self.email_send_daily_limit: int = _env_int("EMAIL_SEND_DAILY_LIMIT", 80)
        # пауза между письмами, сек (защита от спам-фильтров)
        self.email_send_delay_sec: int = _env_int("EMAIL_SEND_DELAY_SEC", 20)
        # Прогрев ящика: лимит в день = start + step * (дней с первого запуска), не выше daily_limit
        self.email_warmup_start: int = _env_int("EMAIL_WARMUP_START", 20)
        self.email_warmup_step: int = _env_int("EMAIL_WARMUP_STEP", 10)
        # Авто-проверка ответов через IMAP
        self.email_auto_check: bool = _env_bool("EMAIL_AUTO_CHECK", True)
        self.email_check_interval_min: int = _env_int("EMAIL_CHECK_INTERVAL_MIN", 15)
        # за сколько последних дней просматривать входящие при проверке
        self.email_check_days: int = _env_int("EMAIL_CHECK_DAYS", 14)
        self.email_follow_up_days: int = _env_int("EMAIL_FOLLOW_UP_DAYS", 7)

        # Торги-россии.рф
        self.torgi_max_pages: int = _env_int("TORGI_MAX_PAGES", 20)
        self.torgi_fetch_details: bool = _env_bool("TORGI_FETCH_DETAILS", True)
        # Ежедневный автопарсинг
        self.torgi_auto_scrape: bool = _env_bool("TORGI_AUTO_SCRAPE", True)
        self.torgi_auto_scrape_delay_min: int = _env_int("TORGI_AUTO_SCRAPE_DELAY_MIN", 5)
        self.torgi_scrape_interval_hours: int = _env_int("TORGI_SCRAPE_INTERVAL_HOURS", 24)
        self.torgi_scrape_pages: int = _env_int("TORGI_SCRAPE_PAGES", 30)
        # Обновление статусов по detail-страницам у лотов, заканчивающихся в ближайшие N дней
        self.torgi_status_refresh_days: int = _env_int("TORGI_STATUS_REFRESH_DAYS", 7)

    def ensure_dirs(self) -> None:
        for d in (DATA_DIR, PHOTOS_DIR, SNAPSHOT_DIR):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()

