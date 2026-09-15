"""Парсер ответов управляющих: извлечение информации о состоянии и фото."""
from __future__ import annotations

import html
import io
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

log = logging.getLogger("efrsb.responder")


def _strip_html(text: str) -> str:
    """Убрать HTML-разметку (письма часто приходят в HTML)."""
    if not text or "<" not in text:
        return text
    t = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    t = re.sub(r"</(?:p|div|tr|li|h[1-6])>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _strip_quotes(body: str) -> str:
    """Убрать процитированное исходное письмо и служебные хвосты."""
    lines = body.splitlines()
    kept: list[str] = []
    cut_markers = (
        "-----", "_____", "> ", ">", "от:", "from:", "кому:", "to:",
        "писал", "wrote:", "subject:", "тема:", "sent:", "дата:",
    )
    for ln in lines:
        s = ln.strip()
        low = s.lower()
        # строка-цитата
        if s.startswith(">"):
            continue
        # подпись
        if low.startswith("с уважением") or low.startswith("с ув."):
            continue
        # начало блока исходного письма
        if any(low.startswith(m) for m in cut_markers) and len(kept) > 0:
            break
        # наш собственный текст-шаблон в цитате
        if "интересующее имущество автомобиль" in low:
            continue
        if "подскажите возможно получить доп" in low:
            continue
        if "за ранее спасибо" in low:
            continue
        if "с уважением," in low and "смирнов" in low:
            continue
        kept.append(ln)
    return "\n".join(kept).strip()


def parse_condition_info(body: str) -> dict[str, Any]:
    """Извлечь информацию о состоянии из текста ответа.

    Возвращает словарь с ключями:
    - condition_summary: общее описание состояния
    - mileage: пробег
    - engine_state: состояние двигателя
    - body_state: состояние кузова
    - interior_state: состояние салона
    - defects: выявленные дефекты
    - documents_available: наличие документов
    - inspection_possible: возможность осмотра
    - raw_text: исходный текст
    """
    result: dict[str, Any] = {
        "condition_summary": "",
        "mileage": "",
        "engine_state": "",
        "body_state": "",
        "interior_state": "",
        "defects": "",
        "documents_available": "",
        "inspection_possible": "",
        "raw_text": body,
    }

    if not body:
        return result

    # Убираем неразрывные пробелы, HTML-разметку и цитаты
    body = body.replace("\u00a0", " ")
    body = _strip_html(body)
    clean = _strip_quotes(body)
    result["clean_text"] = clean
    if not clean:
        return result

    low = clean.lower()

    # Разбиваем на предложения (по знакам и переносам)
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", clean) if s.strip()]

    def pick(keywords: list[str], limit: int = 300) -> str:
        out: list[str] = []
        for s in sents:
            sl = s.lower()
            if any(k in sl for k in keywords) and s not in out:
                out.append(s)
        return " ".join(out[:2])[:limit]

    # Пробег
    mileage_patterns = [
        r"(?:пробег|проехал|на\s*одометре|одометр)[^\d]{0,20}([\d][\d\s]{2,11})\s*(?:км|km|тыс)?",
        r"([\d][\d\s]{2,11})\s*(?:км|km)\s*(?:пробег|одометр)",
        r"([\d][\d\s]{2,11})\s*(?:км|km)\b",
    ]
    for pat in mileage_patterns:
        m = re.search(pat, low)
        if m:
            result["mileage"] = re.sub(r"\s+", " ", m.group(1)).strip() + " км"
            break

    # Состояние двигателя / ходовой
    result["engine_state"] = pick(
        ["двигател", "мотор", "заводится", "не заводится", "своим ходом", "на ходу",
         "троит", "стучит", "ходов", "коробк", "акпп", "кпп", "исправен"]
    )

    # Кузов / ЛКП
    result["body_state"] = pick(
        ["кузов", "лкп", "краск", "скол", "царапин", "вмятин", "коррози", "ржав", "бампер"]
    )

    # Салон
    result["interior_state"] = pick(["салон", "сидень", "торпедо", "обшивк", "кожа", "тряпк"])

    # Дефекты
    defect_keywords = [
        "дефект", "неисправност", "поломк", "требует ремонт", "не работает",
        "неисправн", "брак", "повреждён", "поврежден", "скол", "царапин",
        "вмятин", "коррозия", "ржавчин", "течь", "подтёк", "не заводится",
    ]
    result["defects"] = pick(defect_keywords, limit=400)

    # Документы
    result["documents_available"] = pick(
        ["документ", "птс", "стс", "ключи", "техпаспорт", "регистрац", "дубликат"]
    )

    # Осмотр / где находится
    result["inspection_possible"] = pick(
        ["осмотр", "стоянк", "адрес", "запис", "приехать", "показать",
         "встрет", "телефон", "находит", "хранится", "нахожд"]
    )

    # Общее описание
    important = pick(
        ["состояние", "пробег", "двигател", "кузов", "документ", "фото",
         "ходом", "стоянк", "исправ", "работоспособ", "комплект"],
        limit=600,
    )
    result["condition_summary"] = important or clean[:500].strip()

    return result


def _clean_fragment(text: str) -> str:
    """Очистить фрагмент текста."""
    text = re.sub(r'\s+', ' ', text).strip()
    # Обрезаем по предложению
    for sep in ['. ', ', ', '; ']:
        idx = text.find(sep)
        if 20 < idx < 200:
            text = text[:idx]
    return text[:300]


def _make_thumb(img: Image.Image, dst: Path, size: int = 480) -> None:
    try:
        im = ImageOps.exif_transpose(img.copy())
        im.thumbnail((size, size), Image.LANCZOS)
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        im.save(dst, "JPEG", quality=82)
    except Exception as e:  # noqa: BLE001
        log.debug("thumb fail %s: %s", dst, e)


def _save_image_blobs(
    lot_id: int,
    blobs: list[tuple[str, bytes]],
    data_dir: Path,
) -> list[str]:
    """Сохранить изображения (JPEG + превью). Возвращает список локальных путей."""
    from app.db import add_photo

    lot_dir = data_dir / "photos" / str(lot_id)
    lot_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for idx, (name, data) in enumerate(blobs):
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            w, h = img.size
            if w < 120 or h < 80:
                continue
            im = ImageOps.exif_transpose(img)
            if im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")

            stem = Path(name or f"photo_{idx}").stem
            safe_stem = re.sub(r"[^\w.\-]", "_", stem)[:40] or f"photo_{idx}"
            fname = f"email_{lot_id}_{idx}_{safe_stem}.jpg"
            filepath = lot_dir / fname
            im.save(filepath, "JPEG", quality=90)

            thumb = lot_dir / f"t_{fname}"
            _make_thumb(im, thumb)
        except Exception as e:  # noqa: BLE001
            log.debug("email photo save failed %s: %s", name, e)
            continue

        rel_path = str(filepath.relative_to(data_dir))
        rel_thumb = str(thumb.relative_to(data_dir))
        add_photo(
            lot_id,
            f"email:{name}",
            source_site="email_response",
            local_path=rel_path,
            thumb_path=rel_thumb,
            width=w,
            height=h,
            status="ok",
        )
        saved.append(rel_path)
        log.info("сохранено фото из email: %s -> %s", name, rel_path)

    return saved


def _is_image(filename: str, data: bytes) -> bool:
    """Проверить является ли файл изображением."""
    img_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}
    ext = Path(filename).suffix.lower()
    if ext in img_exts:
        return True

    # Проверяем magic bytes
    if len(data) >= 4:
        if data[:3] == b'\xff\xd8\xff':  # JPEG
            return True
        if data[:8] == b'\x89PNG\r\n\x1a\n':  # PNG
            return True
        if data[:3] == b'GIF':  # GIF
            return True
        if data[:2] == b'BM':  # BMP
            return True
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':  # WEBP
            return True
    return False


MAX_PDF_IMAGES = 60
MAX_ZIP_DEPTH = 2
MAX_ATTACH_BYTES = 80 * 1024 * 1024


def _process_pdf(data: bytes) -> tuple[str, list[tuple[str, bytes]]]:
    """Извлечь текст и встроенные изображения из PDF."""
    try:
        import pymupdf
    except ImportError:
        return "", []
    texts: list[str] = []
    images: list[tuple[str, bytes]] = []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        log.debug("pdf open failed: %s", e)
        return "", []
    try:
        for pno, page in enumerate(doc):
            try:
                t = page.get_text() or ""
                if t.strip():
                    texts.append(t)
            except Exception:  # noqa: BLE001
                pass
            if len(images) >= MAX_PDF_IMAGES:
                continue
            try:
                for img in page.get_images(full=True):
                    xref = img[0]
                    base = doc.extract_image(xref)
                    if base and base.get("image"):
                        ext = base.get("ext", "png")
                        images.append((f"pdf_p{pno + 1}_x{xref}.{ext}", base["image"]))
                        if len(images) >= MAX_PDF_IMAGES:
                            break
            except Exception:  # noqa: BLE001
                pass
    finally:
        doc.close()
    return "\n".join(texts), images


def _process_zip(data: bytes, depth: int = 0) -> tuple[list[str], list[tuple[str, bytes]]]:
    """Распаковать ZIP и извлечь изображения/текст (в т.ч. вложенные PDF/ZIP)."""
    import zipfile

    texts: list[str] = []
    images: list[tuple[str, bytes]] = []
    if depth > MAX_ZIP_DEPTH:
        return texts, images
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        log.debug("zip open failed: %s", e)
        return texts, images

    total = 0
    try:
        for info in zf.infolist():
            if info.is_dir():
                continue
            total += info.file_size
            if total > MAX_ATTACH_BYTES or len(images) >= MAX_PDF_IMAGES:
                break
            name = info.filename
            try:
                blob = zf.read(info)
            except Exception:  # noqa: BLE001
                continue
            low = name.lower()
            if _is_image(name, blob):
                images.append((Path(name).name, blob))
            elif low.endswith(".pdf") or blob[:4] == b"%PDF":
                t, imgs = _process_pdf(blob)
                if t.strip():
                    texts.append(t)
                stem = Path(name).stem
                images.extend((f"{stem}__{n}", d) for n, d in imgs)
            elif low.endswith(".zip") or blob[:2] == b"PK":
                t2, i2 = _process_zip(blob, depth + 1)
                texts.extend(t2)
                images.extend(i2)
    finally:
        zf.close()
    return texts, images


def process_email_response(
    lot_id: int,
    response: dict,
    data_dir: Path,
) -> dict:
    """Обработать ответ: извлечь состояние и сохранить фото (в т.ч. из PDF/ZIP)."""
    from app.db import set_email_status

    body = response.get("body", "")
    attachments = response.get("attachments_raw", [])

    extra_texts: list[str] = []
    image_blobs: list[tuple[str, bytes]] = []

    for filename, data in attachments:
        name = filename or "attachment"
        low = name.lower()
        try:
            if _is_image(name, data):
                image_blobs.append((name, data))
            elif low.endswith(".pdf") or data[:4] == b"%PDF":
                text, imgs = _process_pdf(data)
                if text.strip():
                    extra_texts.append(text)
                stem = Path(name).stem
                image_blobs.extend((f"{stem}__{n}", d) for n, d in imgs)
            elif low.endswith(".zip") or data[:2] == b"PK":
                texts, imgs = _process_zip(data)
                extra_texts.extend(texts)
                image_blobs.extend(imgs)
        except Exception as e:  # noqa: BLE001
            log.debug("attachment %s failed: %s", name, e)

    full_text = body + ("\n" + "\n".join(extra_texts) if extra_texts else "")
    condition = parse_condition_info(full_text)
    if extra_texts:
        condition["attachments_text"] = "\n".join(extra_texts)[:4000]

    saved_photos = _save_image_blobs(lot_id, image_blobs, data_dir) if image_blobs else []

    set_email_status(
        lot_id,
        "responded",
        condition_info=json.dumps(condition, ensure_ascii=False),
        condition_photos=json.dumps(saved_photos, ensure_ascii=False),
        email_response_at=datetime.now(timezone.utc).isoformat(),
    )

    return {
        "condition": condition,
        "photos_saved": len(saved_photos),
    }
