import re
from collections.abc import Callable, Iterator
from contextlib import ExitStack

from playwright.sync_api import Locator, Page, TimeoutError, expect

from egrn import config
from egrn.rosreestr import PAGE_STATE, QUERY, SEARCH, require_session, reset_search, visible_text


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


def _search_query(page: Page, timeout_ms: int, *, retry: bool) -> Locator | None:
    query = page.locator(f"{QUERY}:visible")
    if retry:
        try:
            reset_search(page, timeout_ms)
        except TimeoutError:
            require_session(page)
            if query.count():
                raise
            return None

    require_session(page)
    return query if query.count() else None


def _prepare_attempt(
    page: Page, address: str, timeout_ms: int,
    pause: Callable[[float], None], *, retry: bool,
) -> Locator | None:
    query = _search_query(page, timeout_ms, retry=retry)
    if query is None:
        return None
    if not (
        visible_text(page, "Поиск по адресу и кадастровому номеру").count()
        and visible_text(page, "Выберите значение из справочника").count()
    ):
        return None

    query.fill(address)
    pause(config.ADDRESS_DELAY_SECONDS)
    return page.locator(f"{SEARCH}:visible")


def _wait_for_button(page: Page, button: Locator, *, retries: int) -> bool:
    try:
        expect(button).to_be_enabled(timeout=config.SEARCH_BUTTON_TIMEOUT_MS)
    except AssertionError as error:
        require_session(page)
        if retries >= config.SEARCH_BUTTON_RETRIES:
            raise TimeoutError(
                f"Кнопка «НАЙТИ» не стала активной за {config.SEARCH_BUTTON_TIMEOUT_MS} мс "
                f"даже после {retries} повторов.",
            ) from error
        return False
    return True


def _search_state(page: Page, button: Locator, timeout_ms: int) -> dict[str, str] | None:
    counters = page.locator(f"{TOTAL}:visible")
    counter_count = counters.count()
    if counter_count > 1:
        raise RuntimeError(DUPLICATE_COUNTER)
    with ExitStack() as handles:
        old = counters.element_handle() if counter_count else None
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


def _search_attempts(
    page: Page, address: str, timeout_ms: int, *, pause: Callable[[float], None],
) -> Iterator[Locator | None]:
    attempt = 0
    button_retries = 0
    while attempt < config.SEARCH_ATTEMPTS:
        button = _prepare_attempt(page, address, timeout_ms, pause, retry=bool(attempt or button_retries))
        if button is not None and not _wait_for_button(page, button, retries=button_retries):
            button_retries += 1
            continue
        attempt += 1
        yield button


def submit_search(
    page: Page, address: str, timeout_ms: int, *, pause: Callable[[float], None],
) -> int:
    form_ready = False
    for button in _search_attempts(page, address, timeout_ms, pause=pause):
        form_ready = button is not None
        if button is None:
            continue
        state = _search_state(page, button, timeout_ms)
        if state is not None:
            return _result_count(page, state)
    if not form_ready:
        raise RuntimeError(f"Форма поиска недоступна или изменилась после {config.SEARCH_ATTEMPTS} попыток.")
    raise TimeoutError(
        f"Не дождались новой выдачи за {config.SEARCH_ATTEMPTS} попытки (тайм-аут: {timeout_ms} мс на попытку).",
    )
