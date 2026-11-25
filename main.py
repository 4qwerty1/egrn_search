import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, closing
from pathlib import Path

from openpyxl import load_workbook
from playwright.sync_api import Page
from tqdm import tqdm

import results
import rosreestr
import search

SEARCH_DELAY_SECONDS = 5
EXIT_INTERRUPTED = 130


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
        if not book.has_success(account, address):
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
        addresses = _read_addresses(root / "addresses.xlsx")
        with closing(results.load_results(root / "result.xlsx")) as book:
            pending = _pending_addresses(book, addresses)
            if pending:
                with self._open_page() as page:
                    rosreestr.prepare_page(page, self._timeout_ms)
                    self._search_pending(page, book, pending)

    def _search_pending(
        self, page: Page, book: results.ResultSheet, pending: dict[str, list[str]],
    ) -> None:
        last_index = len(pending) - 1
        with tqdm(
            total=len(pending), desc="Поиск ЕГРН", unit="адр.",
            file=sys.stdout, disable=None, dynamic_ncols=True,
        ) as progress:
            for index, address in enumerate(pending):
                self._address = address
                if index:
                    rosreestr.require_session(page)
                    self._pause(SEARCH_DELAY_SECONDS)
                match = search.lookup(page, address, self._timeout_ms)
                rosreestr.require_session(page)
                book.save(address, pending[address], match)
                progress.update()
                if match is not None and index < last_index:
                    search.close_card(page)


def run(
    root: Path,
    *,
    open_page: Callable[[], AbstractContextManager[Page]] = rosreestr.open_browser,
    pause: Callable[[float], None] = time.sleep,
    timeout_ms: int = rosreestr.DEFAULT_TIMEOUT_MS,
) -> int:
    return _LookupRun(open_page, pause, timeout_ms).execute(root)


if __name__ == "__main__":
    sys.exit(run(Path(__file__).resolve().parent))
