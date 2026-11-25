import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, closing, contextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from playwright.sync_api import Locator, Page, expect, sync_playwright


ORIGIN = "https://lk.rosreestr.ru"
SERVICE_PATH = "/eservices/real-estate-objects-online"
SERVICE_URL = ORIGIN + SERVICE_PATH
ACCOUNT = 'a[href="/my-applications"]'
CAPTCHA = "#captcha"
QUERY = "#query"
SEARCH = "#realestateobjects-search"
TOTAL = "span.realestateobjects-wrapper__results__total-count"
RESULT_ROW = '[role="row"].rros-ui-lib-table__row'
NO_RESULTS = "По заданным критериям поиска объекты не найдены"
CARD = ".realestate-object-modal .build-card-wrapper.card"
FIELD = "build-card-wrapper__info__ul__subinfo"
SHEET = "Результаты"
HEADERS = ("Адрес", "Успешен ли поиск", "Форма собственности", "Название файла скриншота")

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


@contextmanager
def open_browser() -> Iterator[Page]:
    with sync_playwright() as playwright:
        with closing(playwright.chromium.launch(headless=False)) as browser:
            with closing(browser.new_context(viewport={"width": 1900, "height": 930})) as context:
                yield context.new_page()


def publish(path: Path, write: Callable[[Path], object]) -> None:
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
        temporary = Path(file.name)
    try:
        write(temporary)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def load_results(path: Path) -> tuple[Worksheet, dict[str, int]]:
    if not path.exists():
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = SHEET
        sheet.append(HEADERS)
        publish(path, workbook.save)
        return sheet, {}

    workbook = load_workbook(path)
    if SHEET not in workbook:
        raise ValueError(f"{path.name}: нет листа «{SHEET}».")
    sheet = workbook[SHEET]
    values = sheet.values
    header = next(values, ())
    if header[:4] != HEADERS or any(value is not None for value in header[4:]):
        raise ValueError(f"{path.name}: неверные заголовки листа «{SHEET}».")

    rows = {}
    for number, values_in_row in enumerate(values, start=2):
        if all(value is None for value in values_in_row):
            continue
        address, found = values_in_row[:2]
        if not isinstance(address, str) or not address.strip():
            raise ValueError(f"{path.name}: нет адреса в строке {number}.")
        if address in rows:
            raise ValueError(f"{path.name}: повтор адреса {address!r} в строке {number}.")
        if type(found) is not bool:
            raise ValueError(f"{path.name}: в строке {number} флаг поиска не Boolean.")
        rows[address] = number
    return sheet, rows


def save_result(
    sheet: Worksheet,
    path: Path,
    row: int | None,
    address: str,
    result: tuple[str, bytes] | None,
) -> None:
    ownership = filename = None
    if result is not None:
        ownership, png = result
        filename = sha256(address.encode("utf-8")).hexdigest() + ".png"
        screenshots = path.parent / "screenshots"
        screenshots.mkdir(exist_ok=True)
        publish(screenshots / filename, lambda temporary: temporary.write_bytes(png))

    if row is None:
        row = sheet.max_row + 1
    for column, value in enumerate((address, result is not None, ownership, filename), start=1):
        cell = sheet.cell(row, column)
        cell.value = value
        if isinstance(value, str):
            cell.data_type = "s"  # Внешний текст, в том числе начинающийся с «=», не формула.
    publish(path, sheet.parent.save)


def require_session(page: Page) -> None:
    if error := page.evaluate("() => {" + PAGE_STATE + "return sessionError(); }"):
        raise RuntimeError(error)


def prepare_page(page: Page, timeout_ms: int) -> None:
    page.set_default_timeout(timeout_ms)
    response = page.goto(SERVICE_URL, wait_until="domcontentloaded")
    if response is None or response.status >= 400:
        status = "нет ответа" if response is None else f"HTTP {response.status}"
        raise RuntimeError(f"Не удалось открыть страницу поиска Росреестра: {status}.")

    if not page.locator(f"{ACCOUNT}:visible").count() or page.locator(f"{CAPTCHA}:visible").count():
        input(
            "Войдите вручную в Личный кабинет Росреестра в открытом браузере, "
            "вернитесь на страницу поиска и нажмите Enter здесь: "
        )
    page.wait_for_function(
        "() => {" + PAGE_STATE
        + f"return !sessionError() && shown({QUERY!r}).length && shown({SEARCH!r}).length; }}",
        timeout=min(timeout_ms, 30_000),
    ).dispose()


def visible_text(page: Page, text: str) -> Locator:
    return page.get_by_text(text, exact=True).filter(visible=True)


def submit_search(page: Page, address: str, timeout_ms: int) -> int:
    require_session(page)
    if not (
        visible_text(page, "Поиск по адресу и кадастровому номеру").count()
        and visible_text(page, "Выберите значение из справочника").count()
    ):
        raise RuntimeError("Изменился тип поиска или заполнен вид объекта.")

    page.locator(f"{QUERY}:visible").fill(address)
    button = page.locator(f"{SEARCH}:visible")
    expect(button).to_be_enabled(timeout=timeout_ms)
    counters = page.locator(f"{TOTAL}:visible")
    if (previous_count := counters.count()) > 1:
        raise RuntimeError("Видно несколько счётчиков результатов.")
    old = counters.element_handle() if previous_count else None
    try:
        require_session(page)
        button.click()
        fresh = page.wait_for_function(
            "old => {" + PAGE_STATE + f"""
                const error = sessionError();
                if (error) return {{error}};
                const counters = shown({TOTAL!r});
                if (counters.length > 1) return {{error: 'Видно несколько счётчиков результатов.'}};
                const current = counters[0];
                return current && current !== old && {{text: current.innerText.trim()}};
            }}
            """,
            arg=old,
            timeout=timeout_ms,
        )
        try:
            state = fresh.json_value()
        finally:
            fresh.dispose()
    finally:
        if old is not None:
            old.dispose()

    if error := state.get("error"):
        raise RuntimeError(error)
    require_session(page)
    if not (match := re.fullmatch(r"Найдено результатов:\s*(\d+)", state["text"])):
        raise RuntimeError(f"Неизвестное значение счётчика: {state['text']!r}.")
    return int(match[1])


def field_value(card: Locator, label: str) -> str:
    name = card.locator(f"li.{FIELD} > span.{FIELD}__name").filter(
        has_text=re.compile(rf"^\s*{re.escape(label)}\s*$")
    )
    if name.count() != 1:
        raise RuntimeError(f"В карточке отсутствует или дублируется поле «{label}».")
    value = name.locator("..").locator(f":scope > div.{FIELD}__options")
    if value.count() != 1:
        raise RuntimeError(f"Не найден единственный контейнер значения поля «{label}».")
    return value.inner_text().strip()


def lookup(page: Page, address: str, timeout_ms: int) -> tuple[str, bytes] | None:
    count = submit_search(page, address, timeout_ms)
    rows = page.locator(f"{RESULT_ROW}:visible")
    empty = visible_text(page, NO_RESULTS)
    if count == 0:
        empty.first.wait_for()
        if rows.count():
            raise RuntimeError("Нулевой счётчик результатов при наличии строк выдачи.")
        return None

    row = rows.first
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
    if field_value(card, "Кадастровый номер") != number:
        raise RuntimeError(f"Кадастровый номер карточки не совпадает с номером результата {number!r}.")
    ownership = field_value(card, "Форма собственности")
    require_session(page)
    return ownership, card.screenshot(type="png", animations="disabled", scale="css", timeout=30_000)


def close_card(page: Page) -> None:
    page.get_by_role(
        "button", name=re.compile(r"^Вернуться к результатам поиска$", re.IGNORECASE)
    ).and_(page.locator(".realestate-object-modal__btn")).click()
    page.locator(f"{CARD}:visible").wait_for(state="hidden")


def run(
    root: Path,
    *,
    open_page: Callable[[], AbstractContextManager[Page]] = open_browser,
    pause: Callable[[float], None] = time.sleep,
    timeout_ms: int = 30_000,
) -> int:
    address = None
    try:
        lines = (root / "addresses.txt").read_text(encoding="utf-8-sig").splitlines()
        addresses = dict.fromkeys(filter(None, map(str.strip, lines)))
        path = root / "result.xlsx"
        sheet, rows = load_results(path)
        with closing(sheet.parent):
            pending = [
                key for key in addresses
                if key not in rows or sheet.cell(rows[key], 2).value is not True
            ]
            if not pending:
                return 0
            with open_page() as page:
                prepare_page(page, timeout_ms)
                for index, address in enumerate(pending):
                    if index:
                        require_session(page)
                        pause(5)
                    result = lookup(page, address, timeout_ms)
                    require_session(page)
                    save_result(sheet, path, rows.get(address), address, result)
                    if result is not None and index + 1 < len(pending):
                        close_card(page)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        prefix = "Ошибка" if address is None else f"Ошибка для адреса {address!r}"
        print(f"{prefix}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run(Path(__file__).resolve().parent))