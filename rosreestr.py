import builtins
from collections.abc import Generator
from contextlib import closing, contextmanager
from http import HTTPStatus

from playwright.sync_api import Locator, Page, sync_playwright


ORIGIN = "https://lk.rosreestr.ru"
SERVICE_PATH = "/eservices/real-estate-objects-online"
SERVICE_URL = f"{ORIGIN}{SERVICE_PATH}"
ACCOUNT = 'a[href="/my-applications"]'
CAPTCHA = "#captcha"
QUERY = "#query"
SEARCH = "#realestateobjects-search"
DEFAULT_TIMEOUT_MS = 30_000

PAGE_STATE = f"""
    const visible = node => {{
        const {{width, height}} = node.getBoundingClientRect();
        return width > 0 && height > 0 && getComputedStyle(node).visibility === 'visible';
    }};
    const shown = selector => Array.from(document.querySelectorAll(selector)).filter(visible);
    const sessionError = () => {{
        if (location.origin !== {ORIGIN!r} || location.pathname !== {SERVICE_PATH!r})
            return 'Открыта не страница поиска Росреестра.';
        if (shown({CAPTCHA!r}).length) return 'Появилась CAPTCHA.';
        if (!shown({ACCOUNT!r}).length) return 'Нет признака входа в Личный кабинет.';
        return null;
    }};
"""
SEARCH_READY = f"""
() => {{
    {PAGE_STATE}
    return !sessionError() && shown({QUERY!r}).length && shown({SEARCH!r}).length;
}}"""


@contextmanager
def open_browser() -> Generator[Page]:
    with (
        sync_playwright() as playwright,
        closing(playwright.chromium.launch(headless=False)) as browser,
        closing(browser.new_context(viewport={"width": 1900, "height": 930})) as context,
    ):
        yield context.new_page()


def require_session(page: Page) -> None:
    error = page.evaluate(f"() => {{{PAGE_STATE}return sessionError(); }}")
    if error:
        raise RuntimeError(error)


def _has_login(page: Page) -> bool:
    if not page.locator(f"{ACCOUNT}:visible").count():
        return False
    return not page.locator(f"{CAPTCHA}:visible").count()


def prepare_page(page: Page, timeout_ms: int) -> None:
    page.set_default_timeout(timeout_ms)
    response = page.goto(SERVICE_URL, wait_until="domcontentloaded")
    if response is None or response.status >= HTTPStatus.BAD_REQUEST:
        status = "нет ответа" if response is None else f"HTTP {response.status}"
        raise RuntimeError(f"Не удалось открыть страницу поиска Росреестра: {status}.")

    if not _has_login(page):
        builtins.input(
            "Войдите вручную в Личный кабинет Росреестра в открытом браузере, "
            "вернитесь на страницу поиска и нажмите Enter здесь: "
        )
    page.wait_for_function(SEARCH_READY, timeout=min(timeout_ms, DEFAULT_TIMEOUT_MS)).dispose()


def visible_text(page: Page, text: str) -> Locator:
    return page.get_by_text(text, exact=True).filter(visible=True)
