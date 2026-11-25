import re
import time

from playwright.sync_api import Locator, Page

from rosreestr import DEFAULT_TIMEOUT_MS, require_session, visible_text
from search_form import submit_search


RESULT_ROW = '[role="row"].rros-ui-lib-table__row'
NO_RESULTS = "По заданным критериям поиска объекты не найдены"
CARD = ".realestate-object-modal .build-card-wrapper.card"
FIELD = "build-card-wrapper__info__ul__subinfo"
SCREENSHOT_DELAY_SECONDS = 0.5


def _field_text(card: Locator, label: str) -> str:
    name = card.locator(f"li.{FIELD} > span.{FIELD}__name").filter(
        has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"),
    )
    if name.count() != 1:
        raise RuntimeError(f"В карточке отсутствует или дублируется поле «{label}».")
    options = name.locator("..").locator(f":scope > div.{FIELD}__options")
    if options.count() != 1:
        raise RuntimeError(f"Не найден единственный контейнер значения поля «{label}».")
    return options.inner_text().strip()


def _inspect_row(page: Page, row: Locator, empty: Locator) -> tuple[str, bytes]:
    row.wait_for()
    if empty.count():
        raise RuntimeError("Ненулевой счётчик результатов при сообщении об их отсутствии.")
    number = row.locator('[role="cell"]').first.inner_text().strip()
    if not number:
        raise RuntimeError("В первой строке выдачи нет кадастрового номера.")

    require_session(page)
    row.click()
    visible_text(page, "Сведения об объекте").wait_for()
    card = page.locator(f"{CARD}:visible")
    card.wait_for()
    if _field_text(card, "Кадастровый номер") != number:
        raise RuntimeError(f"Кадастровый номер карточки не совпадает с номером результата {number!r}.")
    ownership = _field_text(card, "Форма собственности")
    time.sleep(SCREENSHOT_DELAY_SECONDS)
    require_session(page)
    png = card.screenshot(type="png", animations="disabled", scale="css", timeout=DEFAULT_TIMEOUT_MS)
    return ownership, png


def lookup(page: Page, address: str, timeout_ms: int) -> tuple[str, bytes] | None:
    count = submit_search(page, address, timeout_ms)
    rows = page.locator(f"{RESULT_ROW}:visible")
    empty = visible_text(page, NO_RESULTS)
    if count == 0:
        empty.first.wait_for()
        if rows.count():
            raise RuntimeError("Нулевой счётчик результатов при наличии строк выдачи.")
        return None
    return _inspect_row(page, rows.first, empty)


def close_card(page: Page) -> None:
    back = page.get_by_role(
        "button", name=re.compile(r"^Вернуться к результатам поиска$", re.IGNORECASE),
    )
    back.and_(page.locator(".realestate-object-modal__btn")).click()
    page.locator(f"{CARD}:visible").wait_for(state="hidden")
