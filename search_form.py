import re
from contextlib import ExitStack

from playwright.sync_api import ElementHandle, Locator, Page, expect

from rosreestr import PAGE_STATE, QUERY, SEARCH, require_session, visible_text


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


def _fill_search(page: Page, address: str, timeout_ms: int) -> Locator:
    require_session(page)
    if not (
        visible_text(page, "Поиск по адресу и кадастровому номеру").count()
        and visible_text(page, "Выберите значение из справочника").count()
    ):
        raise RuntimeError("Изменился тип поиска или заполнен вид объекта.")

    page.locator(f"{QUERY}:visible").fill(address)
    button = page.locator(f"{SEARCH}:visible")
    expect(button).to_be_enabled(timeout=timeout_ms)
    return button


def _previous_counter(page: Page) -> ElementHandle | None:
    counters = page.locator(f"{TOTAL}:visible")
    counter_count = counters.count()
    if counter_count > 1:
        raise RuntimeError(DUPLICATE_COUNTER)
    return counters.element_handle() if counter_count else None


def _search_state(page: Page, button: Locator, timeout_ms: int) -> dict[str, str]:
    with ExitStack() as handles:
        old = _previous_counter(page)
        if old is not None:
            handles.callback(old.dispose)
        require_session(page)
        button.click()
        fresh = page.wait_for_function(FRESH_COUNTER, arg=old, timeout=timeout_ms)
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


def submit_search(page: Page, address: str, timeout_ms: int) -> int:
    button = _fill_search(page, address, timeout_ms)
    state = _search_state(page, button, timeout_ms)
    return _result_count(page, state)
