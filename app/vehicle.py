"""Разбор технического описания автомобиля из текста объявления.

Извлекает марку/модель, год, цвет, данные двигателя, кузова, массу,
гос.номер, ПТС/СТС — из свободного текста карточки лота.
"""
from __future__ import annotations

import re

from app.parser import detect_make_model

_WS = re.compile(r"[ \u00a0\t]+")

_MAKE_MODEL_RES = [
    re.compile(r"марка[:\s]+([^,;]+?)[,;]\s*модель[:\s]+([^,;.]+)", re.IGNORECASE),
    re.compile(r"марки\s+([^\s,]+)\s+модель\s+([^\s,.]+)", re.IGNORECASE),
    re.compile(
        r"модель[:\s]+([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9\-]*(?:\s+[A-ZА-ЯЁ0-9][A-Za-zА-Яа-яЁё0-9\-]*){0,2})",
        re.IGNORECASE,
    ),
]

# Известные марки автомобилей (для выделения марки из строки "марка модель")
KNOWN_MAKES = (
    "Toyota Land Cruiser", "Land Rover", "Mercedes-Benz", "Great Wall", "Rolls-Royce",
    "АЗЛК", "ГАЗель", "ТагАЗ", "ГАЗель",
    "Toyota", "Nissan", "Mitsubishi", "Honda", "Mazda", "Subaru", "Suzuki", "Lexus",
    "Hyundai", "Kia", "SsangYong", "Daewoo", "Ravon", "Genesis",
    "Renault", "Peugeot", "Citroen", "Fiat", "Lancia",
    "Volkswagen", "Audi", "Opel", "Ford", "Chevrolet", "Cadillac", "Dodge", "Chrysler",
    "Geely", "Chery", "Haval", "BYD", "JAC", "FAW", "Dongfeng", "Changan", "Lifan",
    "Zotye", "Exeed", "Jetour", "Omoda", "Jaecoo", "Tank", "GAC", "BAIC",
    "Skoda", "Seat", "Volvo", "BMW", "Jaguar", "Porsche", "Jeep", "Lincoln", "Mini",
    "ГАЗ", "УАЗ", "ВАЗ", "КАМАЗ", "ИЖ", "ЗАЗ", "ЗИЛ", "Урал", "МАЗ", "КРАЗ",
    "Lada", "Москвич", "Волга", "MAN", "Scania", "Скания", "DAF", "Iveco", "Isuzu", "Hino",
)

_JUNK_WORDS = {"тс", "автомобиля", "двигателя", "марка", "модель", "автомобиль", "имущества"}

# Транслитерация кириллицы -> латиница (для приведения к единому латинскому стилю)
_CYR_TO_LAT = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E", "Ж": "Zh",
    "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O",
    "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "X", "Ц": "Ts",
    "Ч": "Ch", "Ш": "Sh", "Щ": "Shch", "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E",
    "Ю": "Yu", "Я": "Ya",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}
_TRANS = str.maketrans(_CYR_TO_LAT)

# Алиасы марок: каноническая (латинская) форма для вариантов латиницей/кириллицей
MAKE_ALIASES = {
    # вариации написания латиницей
    "ki": "Kia", "kia": "Kia", "hyundai": "Hyundai", "hundai": "Hyundai",
    "hyndai": "Hyundai", "huyndai": "Hyundai", "toyota": "Toyota", "toyta": "Toyota",
    "nissan": "Nissan", "mitsubishi": "Mitsubishi", "mitsubisi": "Mitsubishi",
    "mitsubishi": "Mitsubishi", "mazda": "Mazda", "honda": "Honda", "subaru": "Subaru",
    "suzuki": "Suzuki", "volkswagen": "Volkswagen", "folkswagen": "Volkswagen",
    "opel": "Opel", "audi": "Audi", "bmw": "BMW", "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz", "mercedes-benz": "Mercedes-Benz",
    "mercedesbenz": "Mercedes-Benz", "skoda": "Skoda", "volvo": "Volvo",
    "renault": "Renault", "peugeot": "Peugeot", "citroen": "Citroen",
    "citroёn": "Citroen", "fiat": "Fiat", "ford": "Ford", "chevrolet": "Chevrolet",
    "chevy": "Chevrolet", "cheverolet": "Chevrolet", "daewoo": "Daewoo",
    "daevo": "Daewoo", "geely": "Geely", "haval": "Haval", "changan": "Changan",
    "lifan": "Lifan", "great wall": "Great Wall", "land rover": "Land Rover",
    "landrover": "Land Rover", "lexus": "Lexus", "infiniti": "Infiniti",
    "porsche": "Porsche", "jaguar": "Jaguar", "jeep": "Jeep", "seat": "Seat",
    "mini": "Mini", "dodge": "Dodge", "chrysler": "Chrysler", "volga": "Volga",
    "uaz": "UAZ", "gaz": "GAZ", "kamaz": "KAMAZ", "moskvich": "Moskvich", "vaz": "Lada",
    "lada": "Lada", "lada vaz": "Lada", "chery": "Chery", "cherry": "Chery",
    "cheri": "Chery", "ssangyong": "SsangYong",
    # кириллица
    "лада": "Lada", "ваз": "Lada", "уаз": "UAZ", "газ": "GAZ", "газель": "GAZ",
    "камаз": "KAMAZ", "москвич": "Moskvich", "волга": "Volga",
    "чери": "Chery", "черри": "Chery", "киа": "Kia", "хюндай": "Hyundai",
    "хендай": "Hyundai", "хундай": "Hyundai", "тойота": "Toyota", "тойта": "Toyota",
    "ниссан": "Nissan", "митсубиси": "Mitsubishi", "митсубиши": "Mitsubishi",
    "мазда": "Mazda", "хонда": "Honda", "субару": "Subaru", "сузуки": "Suzuki",
    "судзуки": "Suzuki", "фольксваген": "Volkswagen", "опель": "Opel", "ауди": "Audi",
    "бмв": "BMW", "мерседес": "Mercedes-Benz", "мерседес-бенц": "Mercedes-Benz",
    "шкода": "Skoda", "вольво": "Volvo", "рено": "Renault", "пежо": "Peugeot",
    "ситроен": "Citroen", "фиат": "Fiat", "форд": "Ford", "шевроле": "Chevrolet",
    "дэу": "Daewoo", "джили": "Geely", "хавал": "Haval", "джип": "Jeep",
    "ленд ровер": "Land Rover", "лексус": "Lexus", "инфинити": "Infiniti",
    "порше": "Porsche", "ягуар": "Jaguar", "сеат": "Seat",
}


def translit_ru(text: str) -> str:
    """Перевести кириллические символы в латиницу."""
    if not text:
        return ""
    return text.translate(_TRANS)


def _norm_key(text: str) -> str:
    return " ".join(text.strip().lower().replace("-", " ").replace("_", " ").split())


def latin_make(make_txt: str) -> str:
    """Привести марку к единому латинскому виду ('Чери'/'Черри'/'Cherry'/'CHERY' -> 'Chery')."""
    if not make_txt:
        return ""
    key = _norm_key(make_txt)
    if key in MAKE_ALIASES:
        return MAKE_ALIASES[key]
    tr = _norm_key(translit_ru(make_txt)).replace(" ", "-")
    tkey = _norm_key(tr)
    if tkey in MAKE_ALIASES:
        return MAKE_ALIASES[tkey]
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9\- ]*", tr):
        return tr.title()
    return ""


def latin_model(model_txt: str) -> str:
    """Привести модель к единому латинскому строю (заглавные): 'Нива'->'NIVA', '4х4'->'4X4'."""
    if not model_txt:
        return ""
    return translit_ru(model_txt.upper()).strip()


def clean_make_model(mm: str | None) -> str:
    """Убрать служебные хвосты из марки/модели ('Lada МОДЕЛЬ' -> 'Lada')."""
    if not mm:
        return ""
    mm = re.sub(r"\s*МОДЕЛЬ\b.*$", "", mm, flags=re.IGNORECASE)
    mm = re.sub(r"\s*Год выпуска\b.*$", "", mm, flags=re.IGNORECASE)
    mm = re.sub(r"\s*VIN\b.*$", "", mm, flags=re.IGNORECASE)
    mm = re.sub(r"\s+", " ", mm).strip(" ,;.-")
    return mm


def split_make_model(mm: str | None) -> tuple[str, str]:
    """Разделить 'марка модель' на (марка, модель). Пустая часть — ''."""
    if not mm:
        return "", ""
    mm = clean_make_model(mm)
    if not mm:
        return "", ""
    lower = mm.lower()
    for m in sorted(KNOWN_MAKES, key=len, reverse=True):
        ml = m.lower()
        if lower == ml:
            return latin_make(m), ""
        if lower.startswith(ml + " "):
            model = mm[len(m):].strip()
            return latin_make(m), latin_model(model)
    parts = mm.split(None, 1)
    make = parts[0]
    if len(make) < 3 or not re.fullmatch(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*", make):
        return "", ""
    if make.lower() in _JUNK_WORDS:
        return "", ""
    model = parts[1].strip() if len(parts) > 1 else ""
    return latin_make(make), latin_model(model)


def _make_model(text: str) -> str:
    for rx in _MAKE_MODEL_RES:
        m = rx.search(text)
        if not m:
            continue
        if m.lastindex and m.lastindex >= 2:
            val = f"{m.group(1).strip()} {m.group(2).strip()}".strip()
        else:
            val = (m.group(1) or "").strip()
        # отрезаем служебные слова
        val = re.split(r"\s+(?:Модель|№|Тип|Цвет|Год)\b", val, flags=re.IGNORECASE)[0].strip()
        if val:
            return clean_make_model(val)
    return clean_make_model(detect_make_model(text))


def _num(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _first(patterns: list[str], text: str) -> str | None:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip(" .,;:-")
            if val:
                return val
    return None


def parse_vehicle_specs(text: str | None) -> dict:
    """Вернуть словарь технических характеристик (пустые ключи опущены)."""
    if not text:
        return {}
    t = _WS.sub(" ", text)

    specs: dict = {}

    year = _first(
        [
            r"год\s+изготовления[:\s]*((?:19|20)\d{2})",
            r"((?:19|20)\d{2})\s*(?:г\.?\s*в\.?|года\s+выпуска|г\.\s*в\b)",
            r"год\s+выпуска[:\s]*((?:19|20)\d{2})",
        ],
        t,
    )
    if not year:
        m = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", t)
        year = m.group(1) if m else None
    if year:
        specs["year"] = int(year)

    color = _first([r"цвет[:\s]*([А-Яа-яЁёA-Za-z\- ]{3,30}?)(?=[,;.]|$)"], t)
    if color:
        specs["color"] = color.strip()

    specs["make_model"] = _make_model(t)

    engine_model = _first([r"модель\s+двигателя[:\s]*([\wА-Яа-яЁё\-\. ]{1,30}?)(?=[,;.]|$)"], t)
    if engine_model:
        specs["engine_model"] = engine_model.strip()

    engine_type = _first([r"тип\s+двигателя[:\s]*([А-Яа-яЁёA-Za-z\- ]{3,30}?)(?=[,;.]|$)"], t)
    if engine_type:
        specs["engine_type"] = engine_type.strip()

    engine_no = _first(
        [
            r"двигател[ья][^,;.]{0,25}?№[:\s]*([A-Za-zА-Яа-яЁё0-9\-]{2,30})",
            r"двигатель\s+№[:\s]*([A-Za-zА-Яа-яЁё0-9\-]{2,30})",
        ],
        t,
    )
    if engine_no:
        specs["engine_no"] = engine_no.strip()

    power = _first(
        [
            r"мощность\s+двигателя[^0-9]{0,25}([\d]+[.,]?\d*)\s*л\.?\s*с",
            r"мощность\s+двигателя[,\s]*л\.?\s*с\.?\s*\(кВт\)[:\s]*([\d]+[.,]?\d*)",
        ],
        t,
    )
    if power:
        specs["engine_power"] = power.replace(",", ".").strip() + " л.с."
    power_kw = _first(
        [r"мощность\s+двигателя[^0-9]{0,25}[\d]+[.,]?\d*\s*\(([\d]+[.,]?\d*)\)", r"([\d]+[.,]?\d*)\s*кВт"],
        t,
    )
    if power_kw:
        specs["engine_power_kw"] = power_kw.replace(",", ".").strip() + " кВт"

    volume = _first(
        [
            r"рабочий\s+объ[её]м\s+двигателя[^:]{0,25}[:\s]*([\d][\d\s]{2,8})\s*(?:куб|см3|см³|л\b)",
            r"объ[её]м\s+двигателя[^0-9]{0,25}([\d][\d\s]{2,8})",
            r"рабочий\s+объ[её]м[^0-9]{0,25}([\d][\d\s]{2,8})",
        ],
        t,
    )
    if volume:
        specs["engine_volume"] = _num(volume)

    body_no = _first([r"кузов[^:№]{0,12}№[:\s]*([A-Za-zА-Яа-яЁё0-9\-]{3,30})"], t)
    if body_no:
        specs["body_no"] = body_no.strip()

    chassis = _first(
        [r"шасси\s*\(?рама\)?[^:№]{0,12}№[:\s]*([A-Za-zА-Яа-яЁё0-9\-]{2,30})"], t
    )
    if chassis and "отсутств" not in chassis.lower():
        specs["chassis_no"] = chassis.strip()

    mass_max = _first([r"разреш[её]нная\s+максимальная\s+масса[:\s]*([\d][\d\s]{2,8})\s*кг"], t)
    if mass_max:
        specs["mass_max"] = _num(mass_max)
    mass_curb = _first([r"масса\s+без\s+нагрузки[:\s]*([\d][\d\s]{2,8})\s*кг"], t)
    if mass_curb:
        specs["mass_curb"] = _num(mass_curb)

    gos = _first(
        [
            r"г[/.]?\s*н[:\s]*([А-ЯA-Z]\d{3}[А-ЯA-Z]{2}\d{2,3})",
            r"гос\.?\s*номер[:\s]*([А-ЯA-Z]\d{3}[А-ЯA-Z]{2}\d{2,3})",
        ],
        t,
    )
    if gos:
        specs["gos_number"] = gos.upper()

    pts = _first([r"ПТС[:\s№–—-]*([\dА-ЯA-Z\- ]{5,25}?)(?=[,;.]|$)"], t)
    if pts and re.search(r"\d", pts):
        specs["pts"] = pts.strip()
    sts = _first([r"СТС[:\s№–—-]*([\dА-ЯA-Z\- ]{5,25}?)(?=[,;.]|$)"], t)
    if sts and re.search(r"\d", sts):
        specs["sts"] = sts.strip()

    return specs
