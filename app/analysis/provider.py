"""Small DeepSeek adapter, fixed HTTPS origin, bounded JSON and no raw error logging."""
from __future__ import annotations

import json
import time

import requests

from .settings import AnalysisSettings


class ProviderError(Exception):
    def __init__(self, code, message):
        self.code, self.public_message = code, message
        super().__init__(message)


class DeepSeekClient:
    ORIGIN = "https://api.deepseek.com"

    def __init__(self, settings: AnalysisSettings, session=None):
        self.settings = settings
        self.session = session or requests.Session()
        self.deadline = time.monotonic() + settings.max_seconds
        self.usage = {"requests": 0, "search_requests": 0, "fetch_requests": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def _post(self, path, payload):
        if not self.settings.api_key:
            raise ProviderError("not_configured", "DeepSeek не подключён")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("deadline", "Время анализа истекло; прежний отчёт сохранён")
        if self.usage["requests"] >= self.settings.max_queries + self.settings.max_pages + 2:
            raise ProviderError("request_budget", "Лимит обращений к DeepSeek для одного анализа исчерпан")
        self.usage["requests"] += 1
        try:
            with self.session.post(
                self.ORIGIN + path, json=payload,
                headers={"Authorization": "Bearer " + self.settings.api_key, "Content-Type": "application/json"},
                timeout=(min(5, remaining), min(60, remaining)), allow_redirects=False, stream=True,
            ) as response:
                status = response.status_code
                if status != 200:
                    messages = {401: "Ключ DeepSeek не принят", 402: "Недостаточно средств у DeepSeek", 403: "Доступ к функции DeepSeek ограничен", 429: "Лимит запросов DeepSeek превышен"}
                    raise ProviderError("http_" + str(status), messages.get(status, "DeepSeek вернул ошибку HTTP " + str(status)))
                parts, size = [], 0
                for part in response.iter_content(65536):
                    if time.monotonic() > self.deadline:
                        raise ProviderError("deadline", "Время анализа истекло")
                    size += len(part)
                    if size > 2_000_000:
                        raise ProviderError("response_too_large", "Ответ DeepSeek превышает допустимый размер")
                    parts.append(part)
                data = json.loads(b"".join(parts), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(data, dict):
                    raise ValueError()
                return data
        except ProviderError:
            raise
        except (requests.RequestException, OSError):
            raise ProviderError("connection", "Нет соединения с DeepSeek или истёк таймаут") from None
        except (ValueError, UnicodeError):
            raise ProviderError("invalid_response", "DeepSeek вернул некорректный JSON") from None

    def chat_json(self, system, content, contract):
        result = self._post("/chat/completions", {
            "model": self.settings.model, "stream": False, "thinking": {"type": "disabled"},
            "temperature": 0.1, "max_tokens": self.settings.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        })
        usage = result.get("usage") or {}
        for name in ("prompt_tokens", "completion_tokens"):
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self.usage[name] += value
        try:
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ProviderError("incomplete", "Ответ модели не завершён; отчёт не сохранён как успешный")
            value = choice["message"]["content"]
            if not isinstance(value, str) or not value.strip():
                raise ValueError()
            data = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            return contract.model_validate(data)
        except ProviderError:
            raise
        except (ValueError, TypeError, KeyError, IndexError):
            raise ProviderError("schema", "Ответ модели не прошёл проверку структуры") from None

    def search(self, query):
        self.usage["search_requests"] += 1
        data = self._post("/v1/tools/search", {"query": query[:128], "count": 10, "need_content": False})
        values = data.get("results")
        if not isinstance(values, list):
            raise ProviderError("search_schema", "Некорректный ответ поиска")
        return values[:10]

    def fetch(self, url):
        self.usage["fetch_requests"] += 1
        data = self._post("/v1/tools/fetch", {"url": url})
        content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("empty_page", "Источник не вернул доступное содержимое")
        return {"title": str(data.get("title") or "")[:300], "content": content[:14000]}
