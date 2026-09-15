"""Браузерная сессия: запуск браузера, прохождение QRATOR, сохранение снапшотов."""
from __future__ import annotations

import logging
import time

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from app.config import Settings, SNAPSHOT_DIR

log = logging.getLogger("efrsb.session")

ANTIDETECT_INIT = """
// маскировка автоматизации
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime: {}};
const _q = window.navigator.permissions && window.navigator.permissions.query;
if (_q) { window.navigator.permissions.query = (p) => (
    p.name === 'notifications' ? Promise.resolve({state: PermissionState.DENIED}) : _q(p)
); }
"""


class BrowserSession:
    """Обёртка над Playwright с переиспользуемым контекстом (cookies сохраняются между запусками)."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.storage_path = settings.data_dir / "storage_state.json"

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self._pw = sync_playwright().start()
        launch_options: dict = {
            "headless": self.s.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=ru-RU",
            ],
        }
        if self.s.browser_channel in ("msedge", "chrome"):
            launch_options["channel"] = self.s.browser_channel
        if self.s.proxy:
            host, _, rest = self.s.proxy.rpartition("://")
            server = self.s.proxy if "@" not in rest else "://".join([host, rest.rsplit("@", 1)[1]])
            auth = None
            if "@" in rest:
                cred = rest.rsplit("@", 1)[0]
                if "://" in cred:
                    _, _, cred = cred.rpartition("://")
                u, _, p = cred.partition(":")
                auth = {"username": u, "password": p}
            launch_options["proxy"] = {"server": server, **(auth or {})}

        self._browser = self._pw.chromium.launch(**launch_options)

        ctx_opts: dict = {
            "user_agent": self.s.user_agent,
            "locale": self.s.locale,
            "timezone_id": "Europe/Moscow",
            "viewport": {"width": 1440, "height": 900},
            "extra_http_headers": {
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": "https://bankrot.fedresurs.ru/",
            },
        }
        if self.storage_path.exists():
            ctx_opts["storage_state"] = str(self.storage_path)
        self._context = self._browser.new_context(**ctx_opts)
        self._context.add_init_script(ANTIDETECT_INIT)

    # ------------------------------------------------------------------ #
    def page(self) -> Page:
        return self._context.pages[0] if self._context.pages else self._context.new_page()

    # ------------------------------------------------------------------ #
    def save_state(self) -> None:
        if self._context:
            try:
                self._context.storage_state(path=str(self.storage_path))
            except Exception as e:  # noqa: BLE001
                log.debug("save_state: %s", e)

    def close(self) -> None:
        try:
            self.save_state()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        finally:
            self._context = None
            self._browser = None
            self._pw = None

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:  # type: ignore[override]
        self.close()

    # ------------------------------------------------------------------ #
    def open_efrsb(self, page: Page, url: str | None = None) -> None:
        """Открывает страницу и ждёт, пока QRATOR-челлендж разрешится."""
        url = url or "https://bankrot.fedresurs.ru/"
        attempts = 0
        while True:
            attempts += 1
            try:
                page.goto(url, timeout=self.s.timeout_ms, wait_until="domcontentloaded")
            except PlaywrightTimeoutError:
                pass
            # QRATOR: 401 -> challenge -> reload / 403-блок
            self._wait_challenge(page)
            blocked = self.is_blocked(page)
            if not blocked:
                return
            if attempts >= 3:
                self.dump_snapshot(page, "qr_blocked")
                raise RuntimeError(
                    "QRATOR (банкrot.fedresurs.ru) блокирует этот IP. "
                    "Используйте прокси (EFRSB_PROXY) или резидентный IP."
                )
            log.warning("QRATOR-блок, повторная попытка %s...", attempts)
            time.sleep(2 + attempts * 2)

    @staticmethod
    def _wait_challenge(page: Page) -> None:
        """QRATOR: короткое ожидание на выполнение qauth_utm PoW/reload."""
        deadline = time.time() + 25
        while time.time() < deadline:
            txt = ""
            try:
                txt = page.locator("body").inner_text(timeout=2000)
            except Exception:  # noqa: BLE001
                pass
            if "403 Error" in txt or "Доступ к сайту" in txt or "Access to bankrot" in txt:
                return
            # если появился контент приложения (не challenge) — норм
            try:
                if page.locator("app-root").count() and \
                        len(page.locator("app-root").first.inner_text(timeout=1500).strip()) > 40:
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.8)

    @staticmethod
    def is_blocked(page: Page) -> bool:
        try:
            url = page.url
            txt = page.locator("body").inner_text(timeout=3000)
        except Exception:  # noqa: BLE001
            return True
        return (
            "qrerror" in url
            or "403 Error" in txt
            or "Доступ к сайту bankrot.fedresurs.ru" in txt
            or "Access to bankrot.fedresurs.ru is forbidden" in txt
        )

    # ------------------------------------------------------------------ #
    def dump_snapshot(self, page: Page, tag: str) -> None:
        """Сохраняет скриншот и HTML для отладки селекторов."""
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        try:
            page.screenshot(path=str(SNAPSHOT_DIR / f"{stamp}_{tag}.png"), full_page=False)
        except Exception as e:  # noqa: BLE001
            log.debug("screenshot fail: %s", e)
        try:
            html = page.content()
            (SNAPSHOT_DIR / f"{stamp}_{tag}.html").write_text(html, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.debug("html dump fail: %s", e)
        log.info("снапшот сохранён: %s_%s", stamp, tag)


def wait_page_load(page: Page, settings: Settings) -> None:
    """Ждёт появления признаков загруженного SPA/контента."""
    from app.scraper import selectors as sel

    for selector in sel.APP_READY_INDICATOR:
        try:
            page.wait_for_selector(selector, timeout=10000)
            return
        except PlaywrightTimeoutError:
            continue
    time.sleep(3)