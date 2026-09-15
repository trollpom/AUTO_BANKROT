"""SQLite-хранилище: лоты, фото, прогоны скрейпинга."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.config import DB_PATH


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Схема
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    efrsb_id TEXT UNIQUE,
    lot_no TEXT,
    title TEXT NOT NULL,
    description TEXT,
    debtor TEXT,
    debtor_inn TEXT,
    trustee TEXT,
    organizer TEXT,
    sale_method TEXT,
    category TEXT,
    vin TEXT,
    make_model TEXT,
    price_amount REAL,
    price_currency TEXT DEFAULT 'RUB',
    region TEXT,
    address TEXT,
    publish_date TEXT,
    end_date TEXT,
    trade_platform TEXT,
    platform_lot_url TEXT,
    source_url TEXT,
    status TEXT DEFAULT 'published',
    photos_found INTEGER DEFAULT 0,
    scrape_status TEXT DEFAULT 'new',
    raw TEXT,
    scraped_at TEXT,
    updated_at TEXT,
    start_price REAL,
    current_price REAL,
    trade_type TEXT,
    trade_form TEXT,
    marketplace TEXT,
    contact_phone TEXT,
    contact_email TEXT,
    contact_name TEXT,
    acquaintance_order TEXT,
    bid_application_order TEXT,
    days_remaining TEXT,
    price_direction TEXT,
    condition_info TEXT,
    condition_photos TEXT,
    email_status TEXT DEFAULT 'not_sent',
    email_sent_at TEXT,
    email_response_at TEXT,
    email_sender TEXT,
    year INTEGER,
    specs TEXT,
    make TEXT,
    model TEXT
);

CREATE INDEX IF NOT EXISTS idx_lots_category ON lots(category);
CREATE INDEX IF NOT EXISTS idx_lots_vin ON lots(vin);
CREATE INDEX IF NOT EXISTS idx_lots_debtor ON lots(debtor);
CREATE INDEX IF NOT EXISTS idx_lots_region ON lots(region);
CREATE INDEX IF NOT EXISTS idx_lots_sale_method ON lots(sale_method);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    source_site TEXT,
    source_page TEXT,
    local_path TEXT,
    thumb_path TEXT,
    caption TEXT,
    width INTEGER,
    height INTEGER,
    status TEXT DEFAULT 'new',
    error TEXT,
    added_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_lot_url ON photos(lot_id, source_url);
CREATE INDEX IF NOT EXISTS idx_photos_lot ON photos(lot_id);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    lots_found INTEGER DEFAULT 0,
    lots_new INTEGER DEFAULT 0,
    photos_found INTEGER DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    kind TEXT,
    lot_id INTEGER,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    price REAL,
    recorded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_price_history_lot ON price_history(lot_id, recorded_at);
"""

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


@contextmanager
def tx():
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    backfill_make_model()
    recompute_lot_timings()
    # Сброс зависших задач при перезапуске
    conn.execute(
        "UPDATE scrape_runs SET status='error', message='прервано перезапуском', "
        "finished_at=? WHERE status='running'",
        (utcnow(),),
    )
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Добавить новые колонки если их нет (миграция для существующих БД)."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(lots)").fetchall()}
    new_cols = {
        "start_price": "REAL", "current_price": "REAL",
        "trade_type": "TEXT", "trade_form": "TEXT", "marketplace": "TEXT",
        "contact_phone": "TEXT", "contact_email": "TEXT", "contact_name": "TEXT",
        "acquaintance_order": "TEXT", "bid_application_order": "TEXT",
        "days_remaining": "TEXT", "price_direction": "TEXT",
        "condition_info": "TEXT", "condition_photos": "TEXT",
        "email_status": "TEXT DEFAULT 'not_sent'",
        "email_sent_at": "TEXT", "email_response_at": "TEXT",
        "year": "INTEGER", "specs": "TEXT",
        "email_sender": "TEXT",
        "make": "TEXT", "model": "TEXT",
    }
    for col, typedef in new_cols.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE lots ADD COLUMN {col} {typedef}")
    idx = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    if "idx_lots_make" not in idx:
        conn.execute("CREATE INDEX idx_lots_make ON lots(make)")
        conn.execute("CREATE INDEX idx_lots_model ON lots(model)")
    if "next_reduce_at" not in existing:
        conn.execute("ALTER TABLE lots ADD COLUMN next_reduce_at TEXT")


def backfill_make_model() -> None:
    """Пересчитать make/model для всех лотов (единый латинский стиль марки/модели)."""
    from app.vehicle import split_make_model

    rows = q(
        """SELECT id, make_model FROM lots
           WHERE make_model IS NOT NULL AND make_model != ''"""
    )
    if not rows:
        return
    with tx() as conn:
        for r in rows:
            mk, md = split_make_model(r["make_model"])
            conn.execute(
                "UPDATE lots SET make=?, model=? WHERE id=?",
                (mk or None, md or None, r["id"]),
            )


def _plural_days(n: int) -> str:
    n10 = n % 10; n100 = n % 100
    if n10 == 1 and n100 != 11:
        return "день"
    if 2 <= n10 <= 4 and not (12 <= n100 <= 14):
        return "дня"
    return "дней"


def recompute_lot_timings() -> int:
    """Пересчитать days_remaining и завершить прошедшие лоты.

    Для публичных предложений — «N дней до снижения цены» (по next_reduce_at),
    а если это последнее ценовое предложение — «N дней до окончания».
    """
    from datetime import date, datetime

    today = datetime.now().date()
    rows = q(
        "SELECT id, status, end_date, days_remaining, trade_form, next_reduce_at FROM lots "
        "WHERE end_date IS NOT NULL AND end_date != ''"
    )
    updates: list[tuple] = []
    for r in rows:
        end_str = r["end_date"][:10]
        try:
            days = (date.fromisoformat(end_str) - today).days
        except ValueError:
            continue
        status = r["status"] or ""
        active = ("заявок" in status.lower() or "объявлены" in status.lower()
                  or "торги объявлены" in status.lower())

        is_public = "публичного предложения" in (r["trade_form"] or "").lower()
        next_str = r["next_reduce_at"] or ""
        if is_public and next_str:
            try:
                target = datetime.fromisoformat(next_str.replace(" ", "T")[:19])
                red_days = (target.date() - today).days
            except ValueError:
                red_days = None
            is_last = not next_str or red_days is None or red_days < 0 or next_str[:10] >= end_str[:10]
            if red_days is not None and red_days >= 0:
                days = red_days
        else:
            is_last = False

        if days < 0:
            new_text = ""
            new_status = "Торги завершены" if active else status
        elif days == 0:
            new_text = "Торги сегодня"
            new_status = status
        elif is_public:
            if is_last:
                new_text = f"{days} {_plural_days(days)} до окончания"
            else:
                new_text = f"{days} {_plural_days(days)} до снижения цены"
            new_status = status
        else:
            new_text = f"{days} {_plural_days(days)} до торгов"
            new_status = status
        if new_text != (r["days_remaining"] or "") or new_status != status:
            updates.append((new_status, new_text, r["id"]))
    if updates:
        with tx() as conn:
            now_iso = utcnow()
            conn.executemany(
                "UPDATE lots SET status=?, days_remaining=?, updated_at=? WHERE id=?",
                [(s, t, now_iso, rid) for s, t, rid in updates],
            )
    return len(updates)


def q(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def q1(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    conn = get_conn()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------
# lots
# --------------------------------------------------------------------------
UPSERT_LOT = """
INSERT INTO lots (
    efrsb_id, lot_no, title, description, debtor, debtor_inn, trustee, organizer,
    sale_method, category, vin, make_model, price_amount, price_currency,
    region, address, publish_date, end_date, trade_platform, platform_lot_url,
    source_url, status, scrape_status, raw, scraped_at, updated_at,
    start_price, current_price, trade_type, trade_form, marketplace,
    contact_phone, contact_email, contact_name,
    acquaintance_order, bid_application_order, days_remaining, price_direction,
    year, specs, make, model, next_reduce_at
) VALUES (
    :efrsb_id, :lot_no, :title, :description, :debtor, :debtor_inn, :trustee, :organizer,
    :sale_method, :category, :vin, :make_model, :price_amount, :price_currency,
    :region, :address, :publish_date, :end_date, :trade_platform, :platform_lot_url,
    :source_url, :status, :scrape_status, :raw, :scraped_at, :updated_at,
    :start_price, :current_price, :trade_type, :trade_form, :marketplace,
    :contact_phone, :contact_email, :contact_name,
    :acquaintance_order, :bid_application_order, :days_remaining, :price_direction,
    :year, :specs, :make, :model, :next_reduce_at
)
ON CONFLICT(efrsb_id) DO UPDATE SET
    lot_no=excluded.lot_no, title=excluded.title, description=excluded.description,
    debtor=excluded.debtor, debtor_inn=excluded.debtor_inn, trustee=excluded.trustee,
    organizer=excluded.organizer, sale_method=excluded.sale_method, category=excluded.category,
    vin=excluded.vin, make_model=excluded.make_model, price_amount=excluded.price_amount,
    price_currency=excluded.price_currency, region=excluded.region, address=excluded.address,
    publish_date=excluded.publish_date, end_date=excluded.end_date,
    trade_platform=excluded.trade_platform, platform_lot_url=excluded.platform_lot_url,
    source_url=excluded.source_url, status=excluded.status, updated_at=excluded.updated_at,
    start_price=excluded.start_price, current_price=excluded.current_price,
    trade_type=excluded.trade_type, trade_form=excluded.trade_form, marketplace=excluded.marketplace,
    contact_phone=excluded.contact_phone, contact_email=excluded.contact_email,
    contact_name=excluded.contact_name,
    acquaintance_order=excluded.acquaintance_order, bid_application_order=excluded.bid_application_order,
    days_remaining=excluded.days_remaining, price_direction=excluded.price_direction,
    year=COALESCE(excluded.year, lots.year), specs=COALESCE(excluded.specs, lots.specs),
    make=COALESCE(excluded.make, lots.make), model=COALESCE(excluded.model, lots.model),
    next_reduce_at=excluded.next_reduce_at
"""


def upsert_lot(data: dict) -> tuple[int, bool]:
    """Возвращает (id, is_new)."""
    with tx() as conn:
        old = conn.execute(
            "SELECT id FROM lots WHERE efrsb_id=?", (data.get("efrsb_id"),)
        ).fetchone()
        is_new = old is None
        lot_id = old["id"] if old else None
        row = _normalize_lot(data)
        if isinstance(row.get("raw"), (dict, list)):
            row["raw"] = json.dumps(row["raw"], ensure_ascii=False)
        conn.execute(UPSERT_LOT, row)
        if lot_id is None:
            now = conn.execute(
                "SELECT id FROM lots WHERE efrsb_id=?", (data.get("efrsb_id"),)
            ).fetchone()
            lot_id = now["id"] if now else conn.execute("SELECT last_insert_rowid() i").fetchone()["i"]
        conn.execute("UPDATE lots SET scraped_at=? WHERE id=?", (utcnow(), lot_id))
        return lot_id, is_new


def record_price(lot_id: int, price: float | int | None) -> bool:
    """Записать точку истории цены, если значение изменилось. Возвращает True при записи."""
    if price is None:
        return False
    conn = get_conn()
    last = conn.execute(
        "SELECT price FROM price_history WHERE lot_id=? ORDER BY recorded_at DESC LIMIT 1",
        (lot_id,),
    ).fetchone()
    try:
        p = float(price)
    except (TypeError, ValueError):
        return False
    if last is not None and abs(last["price"] - p) < 0.01:
        return False
    with tx() as c:
        c.execute(
            "INSERT INTO price_history (lot_id, price, recorded_at) VALUES (?,?,?)",
            (lot_id, p, utcnow()),
        )
    return True


def get_price_history(lot_id: int) -> list[dict]:
    return q(
        "SELECT price, recorded_at FROM price_history WHERE lot_id=? ORDER BY recorded_at ASC",
        (lot_id,),
    )


def seed_price_history(lot_id: int, start_price, current_price, start_at=None) -> None:
    """Первая точка истории: начальная цена (на дату старта) + текущая, если истории ещё нет."""
    from datetime import datetime, timedelta

    c = get_conn().execute(
        "SELECT COUNT(*) c FROM price_history WHERE lot_id=?", (lot_id,)
    ).fetchone()
    if c["c"]:
        return
    rows = []
    try:
        if start_price is not None:
            ts = start_at or (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
            rows.append((lot_id, float(start_price), str(ts).replace(" ", "T")[:19]))
    except (TypeError, ValueError):
        pass
    try:
        if current_price is not None:
            rows.append((lot_id, float(current_price), utcnow()))
    except (TypeError, ValueError):
        pass
    if rows:
        with tx() as conn:
            conn.executemany(
                "INSERT INTO price_history (lot_id, price, recorded_at) VALUES (?,?,?)", rows
            )


_LOT_KEYS = [
    "efrsb_id", "lot_no", "title", "description", "debtor", "debtor_inn", "trustee",
    "organizer", "sale_method", "category", "vin", "make_model", "price_amount",
    "price_currency", "region", "address", "publish_date", "end_date", "trade_platform",
    "platform_lot_url", "source_url", "status", "scrape_status", "raw", "scraped_at",
    "updated_at", "start_price", "current_price", "trade_type", "trade_form", "marketplace",
    "contact_phone", "contact_email", "contact_name",
    "acquaintance_order", "bid_application_order", "days_remaining", "price_direction",
    "year", "specs", "make", "model", "next_reduce_at",
]


def _normalize_lot(data: dict) -> dict:
    row = {}
    for k in _LOT_KEYS:
        row[k] = data.get(k)
    row["updated_at"] = utcnow()
    row.setdefault("scraped_at", utcnow())
    row.setdefault("price_currency", "RUB")
    row.setdefault("scrape_status", "new")
    row.setdefault("status", "published")
    # Авторазбор технического описания (год, цвет, двигатель, кузов, гос.номер, ПТС...)
    if not row.get("specs") or row.get("year") is None:
        try:
            from app.vehicle import parse_vehicle_specs, clean_make_model

            specs = parse_vehicle_specs(
                f"{row.get('title') or ''}\n{row.get('description') or ''}"
            )
            if specs:
                row["specs"] = json.dumps(specs, ensure_ascii=False)
                if row.get("year") is None:
                    row["year"] = specs.get("year")
                if not row.get("make_model") and specs.get("make_model"):
                    row["make_model"] = specs["make_model"]
        except Exception:  # noqa: BLE001
            pass
    if row.get("make_model"):
        try:
            from app.vehicle import clean_make_model, split_make_model

            row["make_model"] = clean_make_model(row["make_model"])
            mk, md = split_make_model(row["make_model"])
            row["make"] = mk or None
            row["model"] = md or None
        except Exception:  # noqa: BLE001
            pass
    return row


def get_lot(lot_id: int) -> dict | None:
    return q1("SELECT * FROM lots WHERE id=?", (lot_id,))


def get_lot_by_efrsb(efrsb_id: str) -> dict | None:
    return q1("SELECT * FROM lots WHERE efrsb_id=?", (efrsb_id,))


def set_lot_props(lot_id: int, **kv) -> None:
    if not kv:
        return
    pairs = ", ".join(f"{k}=?" for k in kv)
    execute(
        f"UPDATE lots SET {pairs}, updated_at=? WHERE id=?",
        [*kv.values(), utcnow(), lot_id],
    )


# Нормализованные типы торгов (trade_form -> короткий вид) для фильтра
TRADE_KINDS: list[tuple[str, str, str]] = [
    ("public_offer", "Публичное предложение", "trade_form LIKE '%публичного предложения%'"),
    ("open_auction", "Открытый аукцион", "trade_form LIKE '%Открытый аукцион%'"),
    ("closed_auction", "Закрытый аукцион", "trade_form LIKE '%Закрытый аукцион%'"),
    ("open_contest", "Открытый конкурс", "trade_form LIKE '%Открытый конкурс%'"),
    ("closed_contest", "Закрытый конкурс", "trade_form LIKE '%Закрытый конкурс%'"),
    ("direct", "Прямая продажа", "trade_form LIKE '%Прямая продажа%'"),
]
TRADE_KIND_SQL = {key: sql for key, _, sql in TRADE_KINDS}
TRADE_KIND_LABEL = {key: label for key, label, _ in TRADE_KINDS}


def list_lots(
    search: str = "",
    category: str = "",
    debtor: str = "",
    region: str = "",
    sale_method: str = "",
    vin: str = "",
    has_photo: bool | None = None,
    sort: str = "published_desc",
    page: int = 1,
    page_size: int = 24,
    only_new: bool = False,
    source: str = "",
    email_status: str = "",
    make: str = "",
    model: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    trade_kind: str = "",
    trade_type: str = "",
    status: str = "",
) -> tuple[list[dict], int, dict]:
    conds: list[str] = []
    params: list[Any] = []

    if search:
        conds.append("(lower(title) LIKE ? OR lower(description) LIKE ? OR lower(make_model) LIKE ?)")
        like = f"%{search.lower()}%"
        params += [like, like, like]
    if category:
        conds.append("category=?")
        params.append(category)
    if debtor:
        conds.append("lower(debtor) LIKE ?")
        params.append(f"%{debtor.lower()}%")
    if region:
        conds.append("region=?")
        params.append(region)
    if sale_method:
        conds.append("sale_method=?")
        params.append(sale_method)
    if vin:
        conds.append("vin LIKE ?")
        params.append(f"%{vin.upper()}%")
    if has_photo is True:
        conds.append("photos_found > 0")
    elif has_photo is False:
        conds.append("(photos_found IS NULL OR photos_found=0)")
    if only_new:
        conds.append("scrape_status='new'")
    if source:
        conds.append("trade_platform LIKE ?")
        params.append(f"%{source}%")
    if make:
        conds.append("make = ?")
        params.append(make)
    if model:
        conds.append("model = ?")
        params.append(model)
    if year_from is not None:
        conds.append("year >= ?")
        params.append(year_from)
    if year_to is not None:
        conds.append("year <= ?")
        params.append(year_to)
    if trade_kind and trade_kind in TRADE_KIND_SQL:
        conds.append(TRADE_KIND_SQL[trade_kind])
    if trade_type:
        conds.append("trade_type = ?")
        params.append(trade_type)
    if status:
        conds.append("status = ?")
        params.append(status)
    if email_status:
        if email_status == "not_sent":
            conds.append("(email_status IS NULL OR email_status=?)")
            params.append("not_sent")
        else:
            conds.append("email_status=?")
            params.append(email_status)

    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = q1(f"SELECT COUNT(*) c FROM lots {where}", params)["c"]

    order_map = {
        "published_desc": "publish_date DESC, id DESC",
        "published_asc": "publish_date ASC, id ASC",
        "price_desc": "price_amount DESC NULLS LAST",
        "price_asc": "price_amount ASC NULLS LAST",
        "photos_desc": "photos_found DESC",
        "updated_desc": "updated_at DESC, id DESC",
    }
    order = order_map.get(sort, order_map["published_desc"])
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    rows = q(
        f"""SELECT lots.*,
            (SELECT thumb_path FROM photos p WHERE p.lot_id=lots.id AND p.status='ok'
             ORDER BY p.id LIMIT 1) AS first_thumb
            FROM lots {where} ORDER BY {order} LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    )
    stats = q1("SELECT COUNT(*) total, SUM(photos_found>0) with_photo FROM lots")
    return rows, total, stats or {"total": 0, "with_photo": 0}


def aggregate(column: str, has_photo_only: bool = False) -> list[dict]:
    cond = "WHERE photos_found > 0" if has_photo_only else ""
    return q(f"SELECT {column} value, COUNT(*) cnt FROM lots {cond} GROUP BY {column} ORDER BY cnt DESC")


# --------------------------------------------------------------------------
# photos
# --------------------------------------------------------------------------
def add_photo(lot_id: int, source_url: str, **kv) -> tuple[int, bool]:
    source_url = (source_url or "").strip()
    if not source_url:
        return 0, False
    with tx() as conn:
        old = conn.execute(
            "SELECT id, status FROM photos WHERE lot_id=? AND source_url=?", (lot_id, source_url)
        ).fetchone()
        if old and old["status"] not in ("error", "queued"):
            return old["id"], False
        if old:  # перезапуск неудачной загрузки
            conn.execute("DELETE FROM photos WHERE id=?", (old["id"],))
        cur = conn.execute(
            """INSERT INTO photos (lot_id, source_url, source_site, source_page, local_path,
               thumb_path, caption, width, height, status, error, added_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lot_id, source_url, kv.get("source_site"), kv.get("source_page"),
             kv.get("local_path"), kv.get("thumb_path"), kv.get("caption"),
             kv.get("width"), kv.get("height"), kv.get("status", "new"),
             kv.get("error"), utcnow()),
        )
        conn.execute("UPDATE lots SET photos_found=(SELECT COUNT(*) FROM photos WHERE lot_id=? AND status IN ('ok','downloading')) WHERE id=?",
                     (lot_id, lot_id))
        return cur.lastrowid, True


def set_photo_status(photo_id: int, status: str, error: str | None = None, **kv) -> None:
    pairs = ["status=?"]
    params: list[Any] = [status]
    for k in ("local_path", "thumb_path", "source_url", "width", "height", "caption", "source_page"):
        if k in kv:
            pairs.append(f"{k}=?")
            params.append(kv[k])
    if error is not None:
        pairs.append("error=?")
        params.append(error)
    params.append(photo_id)
    execute(f"UPDATE photos SET {', '.join(pairs)} WHERE id=?", params)


def list_photos(lot_id: int, status: str = "") -> list[dict]:
    sql = "SELECT * FROM photos WHERE lot_id=?"
    params: list[Any] = [lot_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY id"
    return q(sql, params)


def count_photos(lot_id: int, status: str = "ok") -> int:
    r = q1("SELECT COUNT(*) c FROM photos WHERE lot_id=? AND status=?", (lot_id, status))
    return r["c"] if r else 0


# --------------------------------------------------------------------------
# scrape runs / meta
# --------------------------------------------------------------------------
def start_run() -> int:
    return execute(
        "INSERT INTO scrape_runs (started_at, status) VALUES (?, 'running')", (utcnow(),)
    )


def finish_run(run_id: int, status: str, message: str, lots_found=0, lots_new=0, photos_found=0) -> None:
    execute(
        """UPDATE scrape_runs SET finished_at=?, status=?, message=?, lots_found=?,
           lots_new=?, photos_found=? WHERE id=?""",
        (utcnow(), status, message, lots_found, lots_new, photos_found, run_id),
    )


def update_run(run_id: int, **kv) -> None:
    if not kv:
        return
    cols = ", ".join(f"{k}=?" for k in kv)
    execute(f"UPDATE scrape_runs SET {cols} WHERE id=?", [*kv.values(), run_id])


def last_run() -> dict | None:
    return q1("SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1")


def get_meta(key: str, default: str | None = None) -> str | None:
    r = q1("SELECT value FROM meta WHERE key=?", (key,))
    return r["value"] if r else default


def set_meta(key: str, value: str) -> None:
    execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# --------------------------------------------------------------------------
# Журнал действий (события)
# --------------------------------------------------------------------------
LOG_DIR = DB_PATH.parent / "logs"
LOG_FILE = LOG_DIR / "actions.log"


def _now_local_iso() -> str:
    """Локальное время с часовым поясом, напр. 2026-09-10T15:25:43+03:00."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def add_event(kind: str, message: str, lot_id: int | None = None) -> None:
    ts = _now_local_iso()
    try:
        execute(
            "INSERT INTO events (ts, kind, lot_id, message) VALUES (?,?,?,?)",
            (ts, kind, lot_id, message),
        )
    except Exception:  # noqa: BLE001
        pass
    # Дублируем в лог-файл (полная история за всё время)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        lot_part = f"lot {lot_id}" if lot_id is not None else "-"
        line = f"{ts.replace('T', ' ')[:19]} | {kind:<12} | {lot_part:<8} | {message}"
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def list_events(limit: int = 100, kind: str = "", today: bool = False) -> list[dict]:
    conds = []
    params: list[Any] = []
    if kind:
        conds.append("kind=?")
        params.append(kind)
    if today:
        today_local = datetime.now().astimezone().strftime("%Y-%m-%d")
        conds.append("substr(ts,1,10)=?")
        params.append(today_local)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return q(f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ?", params + [limit])


def count_events(today: bool = False) -> int:
    if today:
        today_local = datetime.now().astimezone().strftime("%Y-%m-%d")
        r = q1("SELECT COUNT(*) c FROM events WHERE substr(ts,1,10)=?", (today_local,))
    else:
        r = q1("SELECT COUNT(*) c FROM events")
    return r["c"] if r else 0


def read_log_tail(lines: int = 1000) -> str:
    """Последние N строк лог-файла (или весь файл при lines<=0)."""
    if not LOG_FILE.exists():
        return ""
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if lines <= 0:
        return content
    parts = content.splitlines()
    return "\n".join(parts[-lines:])


# --------------------------------------------------------------------------
# Email management
# --------------------------------------------------------------------------
# Лот в активной продаже: идёт приём заявок или торги объявлены, срок не истёк.
# По этому условию фильтруются запросы управляющим (проданные/завершённые лоты не трогаем).
ACTIVE_SALE_SQL = (
    "(lots.status LIKE '%заявок%' OR lots.status LIKE '%объявлены%') "
    "AND (lots.end_date IS NULL OR lots.end_date >= datetime('now','localtime'))"
)


def get_lots_for_email(limit: int = 50, no_photos_only: bool = True) -> list[dict]:
    """Получить лоты для отправки email.

    По умолчанию — только лоты БЕЗ успешно скачанных фото (чтобы просить
    фото у управляющего), с email управляющего, без отправленного запроса
    и ТОЛЬКО в активной продаже ('Идёт приём заявок', срок не истёк).
    """
    extra = ""
    if no_photos_only:
        extra = (
            "AND NOT EXISTS (SELECT 1 FROM photos WHERE photos.lot_id=lots.id AND photos.status='ok')"
        )
    return q(
        f"""SELECT * FROM lots
           WHERE (email_status IS NULL OR email_status='not_sent')
           AND contact_email IS NOT NULL AND contact_email != ''
           AND {ACTIVE_SALE_SQL}
           {extra}
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    )


def set_email_status(lot_id: int, status: str, **kv) -> None:
    pairs = ["email_status=?"]
    params: list[Any] = [status]
    for k in ("email_sent_at", "email_response_at", "condition_info", "condition_photos", "email_sender"):
        if k in kv:
            pairs.append(f"{k}=?")
            params.append(kv[k])
    params.append(lot_id)
    execute(f"UPDATE lots SET {', '.join(pairs)} WHERE id=?", params)


def get_bounced_lots_for_email(limit: int = 20) -> list[dict]:
    """Лоты, письма которым не доставлены (повторная отправка со второй почты), в активной продаже."""
    return q(
        f"""SELECT * FROM lots
           WHERE email_status='bounced'
             AND (email_sender IS NULL OR email_sender != 'secondary')
             AND contact_email IS NOT NULL AND contact_email != ''
             AND {ACTIVE_SALE_SQL}
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    )


def get_lots_with_email_status(status: str = "", limit: int = 100) -> list[dict]:
    conds = []
    params: list[Any] = []
    if status:
        conds.append("email_status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return q(f"SELECT * FROM lots {where} ORDER BY id DESC LIMIT ?", params + [limit])


def get_seen_message_ids() -> set[str]:
    """Message-ID уже обработанных писем (чтобы не обрабатывать повторно)."""
    raw = get_meta("email_seen_ids") or "[]"
    try:
        data = json.loads(raw)
        return set(x for x in data if x)
    except (ValueError, TypeError):
        return set()


def add_seen_message_ids(ids: list[str], cap: int = 5000) -> None:
    cur = get_seen_message_ids()
    cur.update(i for i in ids if i)
    lst = list(cur)[-cap:]
    set_meta("email_seen_ids", json.dumps(lst, ensure_ascii=False))
