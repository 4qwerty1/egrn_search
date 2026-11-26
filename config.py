# Имена книг относительно папки проекта; скриншоты и снимок ошибки — рядом с книгой результатов.
ADDRESSES_FILE = "addresses.xlsx"
RESULTS_FILE = "result.xlsx"
RESULTS_SHEET = "Результаты"
SCREENSHOTS_DIR = "screenshots"
ERROR_DETAIL_FILE = "error_detail"

# Размер области страницы Chromium в пикселях.
VIEWPORT_WIDTH = 1900
VIEWPORT_HEIGHT = 930

# Паузы в секундах. Дробные значения допустимы.
SEARCH_DELAY_SECONDS = 4  # Между поисками разных адресов.
ADDRESS_DELAY_SECONDS = 1  # После заполнения адреса, перед нажатием на кнопку поиска.
RESULT_DELAY_SECONDS = 1  # После появления выдачи, в том числе пустой.
CARD_ANALYSIS_DELAY_SECONDS = 1.5  # Перед чтением открывшейся карточки.
AFTER_SCREENSHOT_DELAY_SECONDS = 1  # После снимка карточки, в том числе последнего.

# Тайм-ауты и интервалы Playwright в миллисекундах.
DEFAULT_TIMEOUT_MS = 30_000  # Операции браузера, ожидание выдачи и снимок карточки.
CARD_GRACE_MS = 1_000  # Ожидание карточки после исчезновения индикатора или после клика без него.
CARD_POLL_MS = 25  # Интервал проверки появления карточки.

SEARCH_ATTEMPTS = 3  # Всего попыток получить новую выдачу по одному адресу.
