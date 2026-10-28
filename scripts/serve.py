"""Запуск веб-приложения: python scripts/serve.py"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

from app.config import settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    settings.ensure_dirs()
    print()
    print("  car_lot_pilot запущен")
    print(f"  UI:        http://{settings.host}:{settings.port}/")
    print(f"  API-доки:  http://{settings.host}:{settings.port}/api/docs")
    print()
    uvicorn.run("app.api.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()