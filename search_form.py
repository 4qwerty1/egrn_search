import re
from collections.abc import Callable
from contextlib import ExitStack

from playwright.sync_api import ElementHandle, Locator, Page, TimeoutError, expect

import config
from rosreestr import PAGE_STATE, QUERY, SEARCH, require_session, reset_search, visible_text


TOTAL = "span.realestateobjects-wrapper__results__total-count"
DUPLICATE_COUNTER = "Видно несколько счётчиков результатов."
FRESH_COUNTER = f"""
old => {{
    {PAGE_STATE}
    const error = sessionError();
    if (error) return {{error}};
    const counters = shown({TOTAL!r});
    if (counters.length > 1) return {{error: {DUPLICATE_COUNTER!r}}};
    const current = counters[0];
    return current && current !== old && {{text: current.innerText.trim()}};
}}"""


def _fill_search(
    page: Page, address: str, timeout_ms: int, pause: Callable[[float], None],
) -> Locator:
    require_session(page)
    if not (
        visible_text(page, "Поиск по адресу и кадастровому номеру").count()
        and visible_text(page, "Выберите значение из справочника").count()
    ):
        raise RuntimeError("Изменился тип поиска или заполнен вид объекта.")

    page.locator(f"{QUERY}:visible").fill(address)
    pause(config.ADDRESS_DELAY_SECONDS)
    button = page.locator(f"{SEARCH}:visible")
    expect(button).to_be_enabled(timeout=timeout_ms)
    return button


def _previous_counter(page: Page) -> ElementHandle | None:
    counters = page.locator(f"{TOTAL}:visible")
    counter_count = counters.count()
    if counter_count > 1:
        raise RuntimeError(DUPLICATE_COUNTER)
    return counters.element_handle() if counter_count else None


def _search_state(page: Page, button: Locator, timeout_ms: int) -> dict[str, str] | None:
    with ExitStack() as handles:
        old = _previous_counter(page)
        if old is not None:
            handles.callback(old.dispose)
        require_session(page)
        button.click()
        try:
            fresh = page.wait_for_function(FRESH_COUNTER, arg=old, timeout=timeout_ms)
        except TimeoutError:
            return None
        handles.callback(fresh.dispose)
        return fresh.json_value()


def _result_count(page: Page, state: dict[str, str]) -> int:
    error = state.get("error")
    if error:
        raise RuntimeError(error)
    require_session(page)
    counter_text = state["text"]
    match = re.fullmatch(r"Найдено результатов:\s*(\d+)", counter_text)
    if match is None:
        raise RuntimeError(f"Неизвестное значение счётчика: {counter_text!r}.")
    return int(match[1])


def submit_search(
    page: Page, address: str, timeout_ms: int, *, pause: Callable[[float], None],
) -> int:
    for attempt in range(config.SEARCH_ATTEMPTS):
        if attempt:
            reset_search(page, timeout_ms)
        button = _fill_search(page, address, timeout_ms, pause)
        state = _search_state(page, button, timeout_ms)
        if state is not None:
            return _result_count(page, state)
    raise TimeoutError(
        f"Не дождались новой выдачи за {config.SEARCH_ATTEMPTS} попытки (тайм-аут: {timeout_ms} мс на попытку).",
    )
