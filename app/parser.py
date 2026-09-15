"""Парсинг и нормализация текста сообщений ЕФРСБ: VIN, категория, марка, цена, регион."""
from __future__ import annotations

import re
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# VIN
# ---------------------------------------------------------------------------
VIN_RE = re.compile(r"(?<![0-9A-Za-z])[A-HJ-NPR-Z0-9]{17}(?![0-9A-Za-z])", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
RU_DATE_RE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})")
ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

BRANDS = [
    "lada", "vaz", "уаз", "газ", "газель", "камaz", "камаз", "камаз",
    "kamaaz", "bmw", "mercedes", "mercedes-benz", "audi", "volkswagen", "vw",
    "skoda", "opel", "ford", "chevrolet", "nissan", "toyota", "mitsubishi",
    "mazda", "honda", "subaru", "suzuki", "lexus", "hyundai", "kia", "kia",
    "peugeot", "citroen", "renault", "fiat", "daewoo", "ssangyong", "chery",
    "geely", "haval", "lifan", "uaz", "volvo", "scania", "man", "iveco",
    "daf", "renault", "hino", "isuzu", "maz", "zil", "kraz", "krаz",
    "belaz", "bekaz", "liugong", "xcmg", "kato", "kobelco", "hitachi", "cat",
    "caterpillar", "komatsu", "kubota", "john deere", "case", "new holland",
    "claas", "deutz", "zetor", "minsk", "mtz", "беларус", "кировец", "t-25",
    "t40", "t-40", "юмз", "дт-75", "т-150", "дт-20",
]

MOTORBRAND_ALIAS = {
    "mercedes-benz": "Mercedes-Benz", "volkswagen": "Volkswagen", "w": "W",
}

BRAND_HINTS = {
    "ваз": "ВАЗ", "лада": "Lada", "lada": "Lada", "vaz": "ВАЗ",
    "уаз": "УАЗ", "газ": "ГАЗ", "газель": "ГАЗ/Газель", "камаз": "КАМАЗ",
    "камaz": "КАМАЗ", "камаз": "КАМАЗ",
    "bmw": "BMW", "mercedes": "Mercedes-Benz", "audi": "Audi",
    "toyota": "Toyota", "nissan": "Nissan", "mitsubishi": "Mitsubishi",
    "mazda": "Mazda", "honda": "Honda", "subaru": "Subaru", "suzuki": "Suzuki",
    "lexus": "Lexus", "hyundai": "Hyundai", "kia": "Kia", "kia": "Kia",
    "volkswagen": "Volkswagen", "vw": "VW", "skoda": "Skoda", "opel": "Opel",
    "ford": "Ford", "chevrolet": "Chevrolet", "renault": "Renault",
    "peugeot": "Peugeot", "citroen": "Citroen", "fiat": "Fiat",
    "daewoo": "Daewoo", "ssangyong": "SsangYong", "chery": "Chery",
    "geely": "Geely", "haval": "Haval", "lifan": "Lifan", "volvo": "Volvo",
    "scania": "Scania", "man": "MAN", "iveco": "Iveco", "daf": "DAF",
    "hino": "Hino", "isuzu": "Isuzu", "maz": "МАЗ", "zil": "ЗИЛ",
    "belaz": "БелАЗ", "kato": "KATO", "kobelco": "Kobelco",
    "hitachi": "Hitachi", "caterpillar": "Caterpillar", "cat": "CAT",
    "komatsu": "Komatsu", "kubota": "Kubota", "john deere": "John Deere",
    "claas": "CLAAS", "deutz": "Deutz", "zetor": "Zetor",
    "mtz": "МТЗ", "беларус": "Беларус", "кировец": "Кировец",
}

# Категории логирования
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Мототехника", ["мотоцикл", "мопед", "скутер", "квадроцикл", "питбайк", "мототехник", "снегоход"]),
    ("Водный транспорт", ["катер", "лодка", "судно", "яхт", "баржа", "теплоход", "гидроцикл", "плавсредств"]),
    ("Спецтехника", [
        "экскаватор", "погрузчик", "бульдозер", "трактор", "комбайн", "кран",
        "самосвал", "фронтальный", "автокран", "автовышка", "асфальтоуклад", "каток",
        "бетоносмесит", "бетононасос", "буровая", "скрепер", "грейдер", "экскаватор-погрузчик",
        "манипулятор", "тягач", "лесовоз", "прицеп", "полуприцеп", "цистерна", "автобус",
        "микроавтобус", "фургон", "грузовик", "грузовой", "самосвал", "шасси", "трал",
    ]),
    ("Легковые автомобили", [
        "автомобиль", "легковой", "седан", "хэтчбек", "универсал", "кроссовер", "джип",
        "авто", "седельный", "пикап", "минивэн", "купе",
    ]),
    ("Автобусы/Грузовой", ["автобус", "микроавтобус", "грузовик", "грузовой", "фургон", "прицеп", "полуприцеп"]),
]

SALE_METHOD_RULES = [
    (r"прямая продажа|прямой продажи|без торгов", "Прямая продажа"),
    (r"публичное предложение|путём публичного|посредством публичного", "Публичное предложение"),
    (r"продажа посредством публичного", "Публичное предложение"),
    (r"аукцион", "Аукцион"),
    (r"конкурс", "Конкурс"),
    (r"электронн\w+ торг\w+|торг\w+ в электронной форме", "Электронные торги"),
    (r"продажа имущества", "Продажа имущества"),
]
DEFAULT_SALE_METHOD = "Торги"

PRICE_RE = re.compile(
    r"(?:(?:начальная цена|начальная стоимость|стартовая цена|стартовая стоимость|"
    r"рыночная стоимость|[Сс]тоимость|цена лота|цена)\w*[а-яё ]{0,24}?[:\u2014\-–]?\s*)"
    r"(\d{1,3}(?:[\s\u00a0]\d{3})*(?:[.,]\d{1,2})?)"
    r"\s*(?:руб\w*|₽|р\.)?",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"(\d{1,3}(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?|\d{5,})")
CURRENCY_RE = re.compile(r"(руб(?:лей|ля|ли|ей)?|₽|USD|доллар\w*|\$|EUR|евро|белорусск\w+ руб\w*)", re.IGNORECASE)

REGIONS = [
    "москва", "санкт-петербург", "ленинградская", "московская", "новосибирская",
    "свердловская", "татарстан", "краснодарский", "красноярский", "казань",
    "екатеринбург", "тюменская", "самарская", "нижегородская", "челябинская",
    "республика", "область", "край", "приморский", "хабаровский", "пермский",
    "ростовская", "саратовская", "волгоградская", "воронежская", "иркутская",
    "алтайский", "башкортостан", "удмуртия", "чувашия", "уфа", "пермь", "красноярск",
    "новосибирск", "тюмень", "самара", "нижний новгород", "челябинск", "екатеринбург",
]


def extract_vins(text: str | None) -> list[str]:
    """Все VIN-подобные строки (17 символов, без I/O/Q)."""
    if not text:
        return []
    return list(dict.fromkeys(m.group(0).upper() for m in VIN_RE.finditer(text)))


def is_plausible_vin(s: str) -> bool:
    """Проверка по контрольной сумме VIN (позиции для US/EU) — мягкая."""
    s = s.upper()
    if len(s) != 17 or not VIN_RE.fullmatch(s):
        return False
    translit = "0123456789XJ123456789X"
    weights = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
    try:
        total = 0
        for ch, w in zip(s, weights, strict=False):
            if ch in "IOQ":
                return False
            if ch.isdigit():
                v = int(ch)
            else:
                idx = ord(ch) - ord("A")
                if idx >= len(translit):
                    return False
                v = int(translit[idx])
            total += v * w
        check = total % 11
        check = 10 if check == 10 else check
        last = int(s[8]) if s[8].isdigit() else 10
        return check == last
    except (ValueError, IndexError):
        return False


def best_vin(text: str | None) -> str | None:
    vins = extract_vins(text)
    if not vins:
        return None
    for v in vins:
        if is_plausible_vin(v):
            return v
    return vins[0]


def normalize_vin(vin: str | None) -> str | None:
    if not vin:
        return None
    vin = re.sub(r"[^0-9A-Za-z]", "", vin).upper()
    return vin if len(vin) == 17 else None


def detect_category(text: str | None) -> str:
    if not text:
        return "Автотранспорт"
    low = text.lower()
    found = []
    for cat, kws in CATEGORY_RULES:
        if any(k in low for k in kws):
            found.append((CATEGORY_RULES.index((cat, kws)), cat))
    if not found:
        return "Автотранспорт"
    found.sort()
    # конкретика приоритетнее общих слов
    order = {
        "Мототехника": 1, "Водный транспорт": 1, "Спецтехника": 2,
        "Легковые автомобили": 3, "Автобусы/Грузовой": 2,
    }
    return min(found, key=lambda x: (order.get(x[1], 9), x[0]))[1]


def detect_make_model(text: str | None, vin: str | None = None) -> str:
    if not text:
        return ""
    low = text.lower()
    for brand in BRANDS:
        # ищем как отдельное слово или составное
        pat = re.compile(rf"(?<![a-zа-я0-9]){re.escape(brand)}-?[a-zа-я]*(?![a-zа-я0-9])")
        m = pat.search(low)
        if m:
            token = m.group(0)
            hint = BRAND_HINTS.get(brand) or token.capitalize()
            after = low[m.end():m.end() + 24]
            model = re.match(r"\s*([a-z0-9а-я]{2,12})", after)
            if model:
                return f"{hint} {model.group(1).upper()}"
            return hint
    return ""


def detect_price(text: str | None) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    low = text.lower()
    # точный префиксный паттерн
    m = PRICE_RE.search(low)
    if m:
        try:
            val = float(m.group(1).replace("\u00a0", "").replace(" ", "").replace(",", "."))
        except ValueError:
            val = None
        if val is not None:
            return val, _currency_of(low, m.start(), m.end())
    # фолбэк: число рядом со словом "цена/стоимость" в той же строке
    for nm in NUMBER_RE.finditer(low):
        before = low[max(0, nm.start() - 80):nm.start()]
        after = low[nm.end():nm.end() + 30]
        score = 0
        if re.search(r"(?:начальная|стартовая)\s+цена", before):
            score = 3
        elif re.search(r"\bцена\b|стоимост", before):
            score = 2
        if re.search(r"\bруб|₽|\$|euro|dollar", after):
            score += 1
        if score >= 2:
            try:
                val = float(nm.group(1).replace("\u00a0", "").replace(" ", "").replace(",", "."))
            except ValueError:
                continue
            return val, _currency_of(low, nm.start(), nm.end())
    return None, None


def _currency_of(low: str, start: int, end: int) -> str:
    ctx = low[start:end + 40]
    for c, names in [("USD", ("usd", "доллар", "$")), ("EUR", ("eur", "евро"))]:
        if any(n in ctx.lower() for n in names):
            return c
    return "RUB"


def detect_region(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    for reg in REGIONS:
        if reg in low:
            m = re.search(re.escape(reg), low)
            if m:
                start = max(0, m.start() - 40)
                ctx = low[start:m.start()]
                city = re.findall(r"г\.?\s*([а-яё\- ]{2,24})", ctx)
                if city:
                    return city[-1].strip().title()
                return reg.capitalize()
    return None


def detect_sale_method(text: str | None) -> str:
    if not text:
        return DEFAULT_SALE_METHOD
    low = re.sub(r"\s+", " ", text.lower())
    best = (0, DEFAULT_SALE_METHOD)
    for pat, meth in SALE_METHOD_RULES:
        m = re.search(pat, low)
        if m:
            # публичное предложение — самый специфичный вариант
            score = 3 if "публичное предложение" in pat else 2
            if score > best[0]:
                best = (score, meth)
    return best[1]


def parse_ru_date(text: str | None) -> str | None:
    if not text:
        return None
    m = RU_DATE_RE.search(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = ISO_DATE_RE.search(text)
    if m:
        return m.group(0)


def parse_publication_date(text: str | None) -> str | None:
    return parse_ru_date(text)


def normalize_fio(raw: str | None) -> str:
    if not raw:
        return ""
    parts = re.split(r"[\t\n]+|,|;", raw)
    parts = [p.strip() for p in parts if p.strip()]
    return " ".join(parts)


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def clean_text(t: str | None) -> str:
    if not t:
        return ""
    t = re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u00a0]", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def extract_number_from_title(title: str) -> str | None:
    m = re.search(r"(?:№|N|Н)\s*([0-9]{1,6})", title, re.IGNORECASE)
    return m.group(1) if m else None