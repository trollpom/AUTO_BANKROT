"""Configure the server credential interactively; never put it in command arguments."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request
import warnings

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ORIGIN = "https://api.deepseek.com"


def save_key(path: Path, key: str):
    """Replace only the key, preserving mail configuration and other settings."""
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,256}", key):
        raise ValueError("Неверный формат ключа DeepSeek")
    if path.is_symlink():
        raise ValueError("Файл .env не должен быть символической ссылкой")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines, inserted = [], False
    for line in text.splitlines():
        if re.match(r"^\s*(?:export\s+)?DEEPSEEK_API_KEY\s*=", line):
            if not inserted:
                lines.append("DEEPSEEK_API_KEY=" + key)
                inserted = True
        else:
            lines.append(line)
    if not inserted:
        lines.append("DEEPSEEK_API_KEY=" + key)
    if not re.search(r"(?m)^\s*(?:export\s+)?DEEPSEEK_ANALYSIS_MODEL\s*=", text):
        lines.append("DEEPSEEK_ANALYSIS_MODEL=deepseek-flash")
    fd, temporary = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            if hasattr(os, "fchmod"):
                os.fchmod(output.fileno(), 0o600)
            output.write("\n".join(lines) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_key(key: str, model: str):
    """Read-only models endpoint. No generation, search or financial data."""
    request = urllib.request.Request(ORIGIN + "/models", headers={"Authorization": "Bearer " + key})
    # Do not forward the credential to a redirect destination.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(128001)
            if len(raw) > 128000:
                raise ValueError()
            data = json.loads(raw)
            ids = {x["id"] for x in data["data"] if isinstance(x, dict) and isinstance(x.get("id"), str)}
    except urllib.error.HTTPError as error:
        messages = {401: "DeepSeek не принял ключ", 402: "Недостаточно средств у DeepSeek", 403: "DeepSeek ограничил доступ", 429: "Лимит запросов превышен"}
        raise ValueError(messages.get(error.code, "Ошибка DeepSeek HTTP " + str(error.code))) from None
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Не удалось проверить соединение с DeepSeek") from None
    if model not in ids:
        raise ValueError("Ключ принят, но выбранная модель недоступна. Проверьте DEEPSEEK_ANALYSIS_MODEL")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Безопасная настройка серверного ключа DeepSeek")
    parser.add_argument("--check", action="store_true", help="проверить текущую настройку без её изменения")
    args = parser.parse_args(argv)
    try:
        if not args.check:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    key = getpass.getpass("Ключ DeepSeek (ввод скрыт): ").strip()
            except getpass.GetPassWarning:
                raise ValueError("Терминал не поддерживает скрытый ввод. Запустите команду в обычном терминале") from None
            if not key:
                raise ValueError("Пустой ключ не сохранён")
            save_key(ENV_PATH, key)
            print("Ключ сохранён в локальном .env. Перезапустите сервис после проверки.")
        config = dotenv_values(ENV_PATH)
        key = (os.getenv("DEEPSEEK_API_KEY") or config.get("DEEPSEEK_API_KEY") or "").strip()
        model = (os.getenv("DEEPSEEK_ANALYSIS_MODEL") or config.get("DEEPSEEK_ANALYSIS_MODEL") or "deepseek-flash").strip()
        if not key:
            raise ValueError("Ключ не настроен. Запустите команду без --check")
        if not args.check and os.getenv("DEEPSEEK_API_KEY"):
            print("Внимание: переменная DEEPSEEK_API_KEY окружения имеет приоритет над .env.")
        check_key(key, model)
        print("Соединение проверено: ключ принят, выбранная модель доступна. Платная генерация не запускалась.")
        return 0
    except (ValueError, OSError) as error:
        # Only controlled validation messages are displayed, never credential/request repr.
        print(str(error) if isinstance(error, ValueError) else "Не удалось сохранить локальный .env")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Настройка отменена")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
