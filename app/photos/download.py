"""Скачивание изображений: валидация, сохранение оригинала и ресайз-превью."""
from __future__ import annotations

import io
import logging
import re
from pathlib import Path

import requests
from PIL import Image, ImageOps

from app.config import Settings
from app.db import add_photo, count_photos, get_lot, list_photos, set_photo_status

log = logging.getLogger("efrsb.photos.download")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

DEFAULT_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}

REFERERS = {
    "avito.ru": "https://www.avito.ru/",
    "yandex": "https://yandex.ru/",
    "auto.ru": "https://auto.ru/",
    "drom.ru": "https://www.drom.ru/",
    "bing": "https://www.bing.com/",
    "duckduckgo": "https://duckduckgo.com/",
}


def _referer_for(source_site: str, source_page: str, policy: str) -> str:
    if policy == "site" and source_page:
        return source_page
    for k, v in REFERERS.items():
        if k in (source_site or "").lower():
            return v
    if source_page:
        return source_page
    return "https://www.google.com/"


def _guess_ext(ctype: str, img_format: str | None) -> str:
    if ctype in DEFAULT_EXT:
        return DEFAULT_EXT[ctype]
    if img_format:
        return f".{img_format.lower()}"
    return ".jpg"


def download_photo(
    lot_id: int,
    url: str,
    settings: Settings,
    source_site: str = "",
    source_page: str = "",
    caption: str = "",
) -> tuple[int, str]:
    """Скачать и сохранить фото. Возвращает (photo_id, 'ok'|'error'|'existing')."""
    pid, is_new = add_photo(
        lot_id,
        url,
        source_site=source_site,
        source_page=source_page,
        caption=caption,
        status="downloading",
    )
    if not is_new:
        return pid, "existing"

    url = _normalize_url(url)
    referer = _referer_for(source_site, source_page, settings.referer_policy)

    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": UA,
                "Referer": referer,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
            timeout=(10, 30),
        )
        r.raise_for_status()

        ctype = (r.headers.get("content-type") or "").lower().split(";")[0]
        data = r.content
        if not data:
            raise ValueError("пустое тело ответа")

        img = Image.open(io.BytesIO(data))
        img.load()
        w, h = img.size
        if w < settings.photo_min_width or h < settings.photo_min_height:
            raise ValueError(f"слишком маленькое изображение {w}x{h}")

        lot_dir = settings.photos_dir / str(lot_id)
        lot_dir.mkdir(parents=True, exist_ok=True)
        ext = _guess_ext(ctype, img.format)
        path = lot_dir / f"{pid}{ext}"
        path.write_bytes(data)

        thumb = lot_dir / f"t_{pid}.jpg"
        _make_thumb(img, thumb)

        rel = settings.data_dir
        set_photo_status(
            pid,
            "ok",
            local_path=str(path.relative_to(rel)),
            thumb_path=str(thumb.relative_to(rel)),
            width=w,
            height=h,
        )
        return pid, "ok"
    except Exception as e:  # noqa: BLE001
        set_photo_status(pid, "error", error=str(e)[:400])
        log.debug("скачивание %s: %s", url, e)
        return pid, "error"


def _normalize_url(url: str) -> str:
    """Замена ссылок на превью оригиналами там, где это возможно."""
    u = re.sub(r"/[a-z0-9_]+/cards/{0,1}/(\d{3,4}x\d{3,4})/", "", url)
    u = re.sub(r"(avito\.st/additional|avito\.st/main)/(\d+)", r"\1", u)
    u = re.sub(r"(https://avito\.st)\S+", lambda m: re.sub(r"/\d+x\d+/", "/", m.group(0)), u)
    return u


def _make_thumb(img: Image.Image, dst: Path, size: int = 480) -> None:
    try:
        im = ImageOps.exif_transpose(img.copy())
        im.thumbnail((size, size), Image.LANCZOS)
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        im.save(dst, "JPEG", quality=82)
    except Exception as e:  # noqa: BLE001
        log.debug("thumb fail %s: %s", dst, e)


def photos_for_lot(lot_id: int, status: str = "ok") -> list[dict]:
    return list_photos(lot_id, status)


def already_have(lot_id: int) -> int:
    return count_photos(lot_id, "ok")


def schedule_photo(lot_id: int, url: str, **meta) -> int:
    pid, _ = add_photo(lot_id, url, status="queued", **meta)
    return pid


def photo_path(rel: str, data_dir: Path) -> Path:
    return data_dir / rel