"""Provider settings; never returned to a browser with credentials."""
import os
from dataclasses import dataclass, field


def bounded_int(name, default, low, high):
    try:
        return max(low, min(high, int(os.getenv(name, default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AnalysisSettings:
    api_key: str = field(default="", repr=False)
    model: str = "deepseek-flash"
    max_photos: int = 6
    max_queries: int = 2
    max_pages: int = 6
    daily_limit: int = 20
    cache_minutes: int = 30
    max_seconds: int = 240
    max_tokens: int = 4000

    @classmethod
    def from_env(cls):
        return cls(
            api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            model=os.getenv("DEEPSEEK_ANALYSIS_MODEL", "deepseek-flash").strip() or "deepseek-flash",
            max_photos=bounded_int("AI_ANALYSIS_MAX_PHOTOS", 6, 1, 12),
            max_queries=bounded_int("AI_ANALYSIS_MAX_QUERIES", 2, 1, 3),
            max_pages=bounded_int("AI_ANALYSIS_MAX_PAGES", 6, 1, 10),
            daily_limit=bounded_int("AI_ANALYSIS_DAILY_LIMIT", 20, 1, 200),
            cache_minutes=bounded_int("AI_ANALYSIS_CACHE_MINUTES", 30, 1, 120),
            max_seconds=bounded_int("AI_ANALYSIS_MAX_SECONDS", 240, 30, 600),
            max_tokens=bounded_int("AI_ANALYSIS_MAX_TOKENS", 4000, 512, 8000),
        )

    def public_status(self):
        return {
            "configured": bool(self.api_key), "provider": "deepseek", "model": self.model,
            "max_photos": self.max_photos, "daily_limit": self.daily_limit,
            "message": "Готов к анализу по запросу" if self.api_key else "DeepSeek не подключён. Ключ добавляется на сервере; расчёт по ручным аналогам доступен без ключа.",
        }
