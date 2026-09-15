"""Кандидаты CSS/XPath-селекторов для элементов интерфейса ЕФРСБ.

Сайт ЕФРСБ — Angular SPA, вёрстка меняется. Здесь перечислены несколько
кандидатов для каждого логического элемента; скрапер перебирает их по порядку.
"""

# Пути / контролы на странице поиска
SEARCH_PAGE_URL = "https://fedresurs.ru/sfactmessages/"
BIDDINGS_PAGE_URL = "https://fedresurs.ru/biddings"

# Элементы страницы поиска
SEARCH_INPUT = [
    "input[placeholder*='Поиск']",
    "input[placeholder*='поиск']",
    "input[name*='search']",
    ".search-query input",
    ".query-input",
    ".search-input input",
]
MESSAGE_TYPE_DROPDOWN = [
    ".message-type .ng-select",
    "ng-select[formcontrolname*='messageType']",
    "ng-select[label*='Тип сообщения']",
    ".message-type-select",
]
SEARCH_BUTTON = [
    "button[type='submit']",
    "button:has-text('Найти')",
    ".btn_search",
    ".search-nav__btn",
    "button:has-text('Поиск')",
]
RESULT_TABLE = [
    "table.search-results",
    ".search-results table",
    ".results table",
    "table",
]
RESULT_ROW = ["tbody tr", ".result-row", "tr.row", "._ngcontent tr"]
RESULT_LINK = [
    "td:nth-child(2) a",
    ".col-imp a",
    "td a",
    ".result-row a",
    "a[href*='Message']",
    "td:nth-child(1) a",
]
PAGINATION_NEXT = [
    "a:has-text('Следующая')",
    ".pagination .next",
    "a:has-text('Вперёд')",
    ".pagination-next",
    "a[aria-label='next']",
]
SHOW_MORE = [
    "button:has-text('Показать ещё')",
    ".show-more",
    "button:has-text('Ещё')",
]
MESSAGE_DETAIL_BLOCK = [
    ".message-detail",
    ".message-content",
    ".msg-body",
    ".detail-block",
]
CATEGORY_DROPDOWN = [
    ".category .ng-select",
    "ng-select[formcontrolname*='category']",
    "ng-select[label*='Категория']",
    ".category-select",
]
DATE_FROM = ["input[placeholder*='с']", "input[placeholder*='С']", "[formcontrolname*='dateFrom']"]
DATE_TO = ["input[placeholder*='по']", "[formcontrolname*='dateTo']"]

# Проверка, что SPA загрузился (наличие app-root дочерних элементов)
APP_READY_INDICATOR = [
    "app-root ._ngcontent",
    "app-root app-main",
    ".search-nav",
    "app-root router-outlet ~ *",
]

# QRATOR-капча (fallback)
CAPTCHA_BOX = "#wrapper-captcha, .qrator-captcha"
QRERROR_BODY = "text='Доступ к сайту' , text='Access to bankrot.fedresurs.ru is forbidden'"

LABELS = {
    "debtor": ["Должник", "Должник (ФИО)", "Наименование должника"],
    "trustee": ["Арбитражный управляющий", "Управляющий", "Финансовый управляющий"],
    "organizer": ["Организатор торгов", "Уполномоченный", "Оператор"],
    "sale_method": ["Способ реализации", "Способ продажи", "Способ реализации имущества"],
    "publish_date": ["Дата публикации", "Опубликовано", "Дата составления"],
    "trading_platform": ["Торговая площадка", "Электронная площадка", "Организатор проведения"],
    "price": ["Начальная цена", "Цена лота", "Цена"],
    "lot": ["Лот №", "Предмет торгов", "Наименование имущества", "Продаваемое имущество"],
    "vin": ["VIN", "ВИН", "Номер кузова", "Идентификационный номер"],
}
