"""Экспорт БД в JSON: python scripts/export_json.py out.json [--category Спецтехника]"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, list_lots, list_photos


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="data/export.json")
    ap.add_argument("--category", default="")
    args = ap.parse_args()
    init_db()

    items, total, _ = list_lots(category=args.category, page_size=1000, page=1, has_photo=None)
    for it in items:
        it["photos"] = list_photos(it["id"], status="ok")
    payload = {"total": total, "items": items}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"экспортировано лотов: {len(items)} -> {out}")


if __name__ == "__main__":
    main()