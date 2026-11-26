import re
from collections.abc import Callable, Iterator
from enum import StrEnum

from playwright.sync_api import Locator, Page, expect

from egrn import config
from egrn.card_details import CardMatch, read_card
from egrn.card_wait import MODAL, wait_for_card
from egrn.rosreestr import require_session, visible_text
from egrn.search_form import submit_search


RESULT_ROW = '[role="row"].rros-ui-lib-table__row'
NO_RESULTS = "По заданным критериям поиска объекты не найдены"
ACTIVE_PAGE = "button.rros-ui-lib-table-pagination__btn--number.rros-ui-lib-table-pagination__btn--active"
NEXT_PAGE = "button.rros-ui-lib-table-pagination__btn--next"
RESULT_COUNT_MISMATCH = "Число строк выдачи не совпадает со счётчиком результатов."


class CardOpenFailure(StrEnum):
    EXHAUSTED = "Не получилось открыть карточку объекта"


type LookupResult = CardMatch | CardOpenFailure | None


def _close_incomplete_card(page: Page) -> None:
    if not page.locator(f"{MODAL}:visible").count():
        return
    active = page.locator(f"{ACTIVE_PAGE}:visible")
    previous = active.all_inner_texts()
    close_card(page)
    if previous and active.all_inner_texts() != previous:
        raise RuntimeError("Страница выдачи изменилась после закрытия карточки.")


def _inspect_row(
    page: Page, row: Locator, timeout_ms: int, pause: Callable[[float], None],
) -> CardMatch | None:
    row.wait_for(timeout=timeout_ms)
    number = row.locator('[role="cell"]').first.inner_text(timeout=timeout_ms).strip()
    if not number:
        raise RuntimeError("В строке выдачи нет кадастрового номера.")

    if wait_for_card(page, row, timeout_ms):
        return read_card(page, number, pause)
    _close_incomplete_card(page)
    return None


def _next_result_page(page: Page, page_number: int, timeout_ms: int) -> None:
    require_session(page)
    next_button = page.locator(f"{NEXT_PAGE}:visible")
    if next_button.count() != 1 or not next_button.is_enabled():
        raise RuntimeError(RESULT_COUNT_MISMATCH)
    next_button.click(timeout=timeout_ms)
    active = page.locator(f"{ACTIVE_PAGE}:visible")
    expect(active).to_have_text(str(page_number), timeout=timeout_ms)


def _result_rows(page: Page, rows: Locator, count: int, timeout_ms: int) -> Iterator[Locator]:
    page_number = 1
    remaining = count
    while remaining:
        rows.first.wait_for(timeout=timeout_ms)
        page_size = rows.count()
        if not 0 < page_size <= remaining:
            raise RuntimeError(RESULT_COUNT_MISMATCH)
        for index in range(page_size):
            yield rows.nth(index)
        remaining -= page_size
        if remaining:
            page_number += 1
            _next_result_page(page, page_number, timeout_ms)


def lookup(
    page: Page, address: str, timeout_ms: int, *, pause: Callable[[float], None],
) -> LookupResult:
    count = submit_search(page, address, timeout_ms, pause=pause)
    rows = page.locator(f"{RESULT_ROW}:visible")
    empty = visible_text(page, NO_RESULTS)
    if count == 0:
        empty.first.wait_for()
        if rows.count():
            raise RuntimeError("Нулевой счётчик результатов при наличии строк выдачи.")
        pause(config.RESULT_DELAY_SECONDS)
        return None
    rows.first.wait_for(timeout=timeout_ms)
    if empty.count():
        raise RuntimeError("Ненулевой счётчик результатов при сообщении об их отсутствии.")
    pause(config.RESULT_DELAY_SECONDS)
    for row in _result_rows(page, rows, count, timeout_ms):
        match = _inspect_row(page, row, timeout_ms, pause)
        if match is not None:
            return match
    return CardOpenFailure.EXHAUSTED


def close_card(page: Page) -> None:
    back = page.get_by_role(
        "button", name=re.compile(r"^Вернуться к результатам поиска$", re.IGNORECASE),
    )
    back.and_(page.locator(".realestate-object-modal__btn")).click()
    page.locator(f"{MODAL}:visible").wait_for(state="hidden")
