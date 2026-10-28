"""Minimal outbound context and explicitly selected, sanitized local images."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import warnings
from pathlib import Path

from PIL import Image, ImageOps

from .schemas import Vehicle


def clean_text(value, lot=None, limit=18000):
    text = str(value or "")[:limit * 2]
    for name in ("debtor", "debtor_inn", "trustee", "organizer", "contact_name", "contact_email", "contact_phone", "address"):
        raw = (lot or {}).get(name)
        if raw and len(str(raw)) >= 4:
            text = re.sub(re.escape(str(raw)), "[удалено]", text, flags=re.I)
    text = re.sub(r"[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}", "[email удалён]", text)
    text = re.sub(r"(?<!\w)(?:\+?7|8)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)", "[телефон удалён]", text)
    text = re.sub(r"(?i)\b(?:ИНН|СНИЛС|паспорт)\s*[:№]?\s*[\d\s-]{8,20}", "[идентификатор удалён]", text)
    text = re.sub(r"(?i)\b(?:sk-[\w-]{16,}|ghp_[\w]{20,}|github_pat_[\w_]{30,})\b", "[секрет удалён]", text)
    return text[:limit]


def parse_object(raw):
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def lot_vehicle(lot, override: Vehicle):
    from app.vehicle import split_make_model
    specs = parse_object(lot.get("specs"))
    make, model = split_make_model(lot.get("make_model") or specs.get("make_model"))
    year = lot.get("year") or specs.get("year")
    try:
        year = int(year)
        if not 1950 <= year <= 2100:
            year = None
    except (TypeError, ValueError):
        year = None
    base = {"make": lot.get("make") or make, "model": lot.get("model") or model,
            "year": year, "region": lot.get("region") or ""}
    for name, value in override.model_dump().items():
        if value is not None and value != "":
            base[name] = value
    return Vehicle.model_validate(base)


def snapshot(lot):
    condition = parse_object(lot.get("condition_info"))
    # Whitelist, rather than exporting a DB row, raw email or entire specs document.
    return {
        "lot_id": lot["id"], "title": clean_text(lot.get("title"), lot, 500),
        "description": clean_text(lot.get("description"), lot),
        "manager_report": clean_text("\n".join(str(condition.get(k) or "") for k in (
            "condition_summary", "mileage", "engine_state", "body_state", "interior_state", "defects", "documents_available", "inspection_possible")), lot, 5000),
        "make": lot.get("make"), "model": lot.get("model"), "year": lot.get("year"),
        "current_price": lot.get("current_price"), "price_amount": lot.get("price_amount"),
        "price_currency": lot.get("price_currency"), "region": lot.get("region"),
        "updated_at": lot.get("updated_at"), "email_response_at": lot.get("email_response_at"),
    }


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def prepare_photos(photos, selected_ids, data_dir: Path, max_photos):
    if len(selected_ids) > max_photos:
        raise ValueError("Превышен лимит выбранных фотографий")
    by_id = {p["id"]: p for p in photos if p.get("status") == "ok"}
    blocks, evidence, total = [], [], 0
    root = (data_dir / "photos").resolve()
    for pid in selected_ids:
        photo = by_id.get(pid)
        if not photo or not photo.get("local_path"):
            raise ValueError("Выбранная фотография недоступна или принадлежит другому лоту")
        path = (data_dir / photo["local_path"]).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise ValueError("Неверный путь к фотографии") from None
        if not path.is_file() or path.stat().st_size > 5_000_000:
            raise ValueError("Фотография недоступна или превышает 5 МБ")
        raw = path.read_bytes()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.width * image.height > 24_000_000:
                        raise ValueError()
                    image.seek(0)
                    image = ImageOps.exif_transpose(image).convert("RGB")
                    image.thumbnail((1600, 1600))
                    output = io.BytesIO()
                    image.save(output, "JPEG", quality=85)  # No EXIF or original file metadata.
                    encoded = output.getvalue()
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValueError("Фотографию не удалось безопасно прочитать") from None
        total += len(encoded)
        if total > 12_000_000:
            raise ValueError("Суммарный размер фотографий превышен")
        eid = "photo:" + str(pid)
        blocks.extend([{"type": "text", "text": eid}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")}}])
        evidence.append({"id": eid, "photo_id": pid, "sha256": hashlib.sha256(raw).hexdigest(), "origin": str(photo.get("source_site") or "unknown")[:80], "identity": "user_confirmed"})
    return blocks, evidence


def validate_condition(output, texts, photo_evidence):
    photo_ids = {x["id"] for x in photo_evidence}
    for finding in output.findings:
        if finding.evidence_id in photo_ids:
            if finding.kind == "reported_fact":
                raise ValueError("Модель выдала сообщение о факте вместо наблюдения по фото")
        elif finding.evidence_id in texts:
            quote = " ".join(finding.quote.split())
            source = " ".join(texts[finding.evidence_id].split())
            # A substring of a word (e.g. 'исправен' inside 'неисправен') is not a quote.
            if not quote or not re.search(r"(?<!\w)" + re.escape(quote) + r"(?!\w)", source):
                raise ValueError("Вывод модели не подтверждён цитатой")
        else:
            raise ValueError("Модель сослалась на отсутствующее свидетельство")
    return output.model_dump(mode="json")
