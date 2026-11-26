import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, closing
from pathlib import Path

from openpyxl import load_workbook
from playwright.sync_api import Error, Page
from tqdm import tqdm

from egrn import config, results, rosreestr, search

EXIT_INTERRUPTED = 130


def _save_error_detail(page: Page, path: Path) -> None:
    try:
        page.screenshot(path=path, type="png", full_page=True)
    except (Error, OSError) as error:
        sys.stderr.write(f"Не удалось сохранить снимок страницы в {path.name}: {error}\n")


def _address_fields(
    path: Path, number: int, columns: tuple[object, ...],
) -> tuple[str, str] | None:
    account, address = (
        "" if entry is None else str(entry).strip()
        for entry in columns
    )
    if not (account or address):
        return None
    if not (account and address):
        raise ValueError(f"{path.name}: в строке {number} нужен номер ЛС и адрес.")
    return account, address


def _read_addresses(path: Path) -> dict[tuple[str, str], None]:
    addresses: dict[tuple[str, str], None] = {}
    with closing(load_workbook(path, read_only=True, data_only=True)) as workbook:
        for number, columns in enumerate(
            workbook.worksheets[0].iter_rows(max_col=2, values_only=True),
            start=1,
        ):
            fields = _address_fields(path, number, columns)
            if fields is not None:
                addresses[fields] = None
    return addresses


def _pending_addresses(
    book: results.ResultSheet, addresses: dict[tuple[str, str], None],
) -> dict[str, list[str]]:
    pending: dict[str, list[str]] = {}
    for account, address in addresses:
        if not book.has_result(account, address):
            pending.setdefault(address, []).append(account)
    return pending


class _LookupRun:
    def __init__(
        self,
        open_page: Callable[[], AbstractContextManager[Page]],
        pause: Callable[[float], None],
        timeout_ms: int,
    ) -> None:
        self._open_page = open_page
        self._pause = pause
        self._timeout_ms = timeout_ms
        self._address: str | None = None

    def execute(self, root: Path) -> int:
        try:
            self._run_workbook(root)
        except KeyboardInterrupt:
            return EXIT_INTERRUPTED
        except Exception as error:
            prefix = "Ошибка" if self._address is None else f"Ошибка для адреса {self._address!r}"
            sys.stderr.write(f"{prefix}: {error}\n")
            return 1

        return 0

    def _run_workbook(self, root: Path) -> None:
        addresses = _read_addresses(root / config.ADDRESSES_FILE)
        with closing(results.load_results(root / config.RESULTS_FILE)) as book:
            pending = _pending_addresses(book, addresses)
            if not pending:
                return
            error_detail = (root / config.RESULTS_FILE).with_name(config.ERROR_DETAIL_FILE)
            with self._open_page() as page:
                try:
                    self._search_pending(page, book, pending, error_detail)
                except Exception:
                    _save_error_detail(page, error_detail)
                    raise

    def _search_pending(
        self,
        page: Page,
        book: results.ResultSheet,
        pending: dict[str, list[str]],
        error_detail: Path,
    ) -> None:
        rosreestr.prepare_page(page, self._timeout_ms)
        with tqdm(
            total=len(pending), desc="Поиск ЕГРН", unit="адр.",
            file=sys.stdout, disable=None, dynamic_ncols=True,
        ) as progress:
            for index, address in enumerate(pending, start=1):
                self._address = address
                if index > 1:
                    rosreestr.require_session(page)
                    self._pause(config.SEARCH_DELAY_SECONDS)
                match = search.lookup(page, address, self._timeout_ms, pause=self._pause)
                rosreestr.require_session(page)
                has_card = match is not None and not isinstance(match, search.CardOpenFailure)
                if has_card and match.error is not None:
                    _save_error_detail(page, error_detail)
                book.save(address, pending[address], match)
                progress.update()
                if has_card and index < len(pending):
                    search.close_card(page)


def run(
    root: Path,
    *,
    open_page: Callable[[], AbstractContextManager[Page]] = rosreestr.open_browser,
    pause: Callable[[float], None] = time.sleep,
    timeout_ms: int = config.DEFAULT_TIMEOUT_MS,
) -> int:
    return _LookupRun(open_page, pause, timeout_ms).execute(root)
