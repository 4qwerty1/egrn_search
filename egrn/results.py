import os
import re
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, cast

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet

from egrn import config

if TYPE_CHECKING:
    from egrn.search import LookupResult


HEADERS = ("Номер ЛС", "Адрес", "Успешен ли поиск", "Форма собственности", "Название файла скриншота")
FOUND_COLUMN = 3
SUCCESS_STATUS = "Да"
INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])(?: *\.|$)",
    re.IGNORECASE | re.ASCII,
)
MAX_SCREENSHOT_STEM_BYTES = 244  # Оставляем место для «_» перед именем устройства и «_00000.png».
SCREENSHOT_SUFFIX_COUNT = 0x100000
TEMP_PREFIX_NAME_LENGTH = 32

type SheetRow = tuple[object, ...]
type ResultFields = tuple[str, str, str, str | None, str | None]
type RowIndex = dict[tuple[str, str], int]


class ResultSheet:
    def __init__(
        self, path: Path, workbook: Workbook, sheet: Worksheet, rows: RowIndex,
    ) -> None:
        self._path: Path = path
        self._workbook: Workbook = workbook
        self._sheet: Worksheet = sheet
        self._rows = rows

    def has_result(self, account: str, address: str) -> bool:
        return (account, address) in self._rows

    def save(
        self, address: str, accounts: list[str],
        match: "LookupResult",
    ) -> None:
        found = "Адрес не найден"
        ownership = None
        filename = None
        if isinstance(match, str):
            found = str(match)
        elif match is not None:
            found = match.error or SUCCESS_STATUS
            ownership = match.ownership
            filename = _store_screenshot(self._path.parent, address, match.png)

        for account in accounts:
            self._write_row((account, address, found, ownership, filename))
        _publish(self._path, self._workbook.save)

    def close(self) -> None:
        self._workbook.close()

    def _write_row(self, fields: ResultFields) -> None:
        key = fields[:2]
        row = self._rows.get(key)
        if row is None:
            row = self._sheet.max_row + 1
            self._rows[key] = row
        for column, entry in enumerate(fields, start=1):
            cell = cast(Cell, self._sheet.cell(row, column))
            cell.value = entry
            if isinstance(entry, str):
                cell.data_type = "s"  # Внешний текст, в том числе начинающийся с «=», не формула.


def _publish(path: Path, write: Callable[[Path], object]) -> None:
    prefix = path.name[:TEMP_PREFIX_NAME_LENGTH]
    with NamedTemporaryFile(dir=path.parent, prefix=f".{prefix}.", delete=False) as stream:
        temporary = Path(stream.name)
    with ExitStack() as cleanup:
        cleanup.callback(temporary.unlink, missing_ok=True)
        write(temporary)
        os.replace(temporary, path)
        cleanup.pop_all()


def _store_screenshot(root: Path, address: str, png: bytes) -> str:
    stem = INVALID_FILENAME_CHARACTERS.sub("", address)
    stem = stem.encode()[:MAX_SCREENSHOT_STEM_BYTES].decode(errors="ignore")
    stem = stem.rstrip(" .") or "Адрес"
    if WINDOWS_DEVICE_NAME.match(stem):
        stem = f"_{stem}"
    screenshots = root / config.SCREENSHOTS_DIR
    screenshots.mkdir(exist_ok=True)
    path = screenshots / f"{stem}.png"
    suffix = 0
    while path.exists(follow_symlinks=False):
        if suffix == SCREENSHOT_SUFFIX_COUNT:
            raise FileExistsError(f"Нет свободного имени для скриншота: {stem}")
        name = "{0}_{1:05x}.png".format(stem, suffix)
        path = screenshots / name
        suffix += 1
    _publish(path, lambda temporary: temporary.write_bytes(png))
    return path.name


def _validate_header(path: Path, header: SheetRow) -> None:
    width = len(HEADERS)
    overflow = header[width:]
    has_extra_cells = any(entry is not None for entry in overflow)
    if header[:width] != HEADERS or has_extra_cells:
        raise ValueError(f"{path.name}: неверные заголовки листа «{config.RESULTS_SHEET}».")


def _check_key(path: Path, number: int, columns: SheetRow) -> tuple[str, str]:
    account, address = columns[:2]
    if not isinstance(account, str) or not account.strip():
        raise ValueError(f"{path.name}: нет номера ЛС в строке {number}.")
    if not isinstance(address, str) or not address.strip():
        raise ValueError(f"{path.name}: нет адреса в строке {number}.")
    found = columns[FOUND_COLUMN - 1]
    has_message = isinstance(found, str) and found.strip()
    if not isinstance(found, bool) and not has_message:
        raise ValueError(f"{path.name}: в строке {number} нужен логический статус поиска или текст ошибки.")
    return account, address


def _index_rows(path: Path, row_entries: Iterator[SheetRow]) -> RowIndex:
    rows: RowIndex = {}
    for number, columns in enumerate(row_entries, start=2):
        if all(entry is None for entry in columns):
            continue
        key = _check_key(path, number, columns)
        if key in rows:
            raise ValueError(f"{path.name}: повтор пары ЛС и адреса {key!r} в строке {number}.")
        rows[key] = number
    return rows


def load_results(path: Path) -> ResultSheet:
    if not path.exists():
        workbook = Workbook()
        sheet = cast(Worksheet, workbook.active)
        sheet.title = config.RESULTS_SHEET
        sheet.append(HEADERS)
        _publish(path, workbook.save)
        return ResultSheet(path, workbook, sheet, {})

    workbook = load_workbook(path)
    if config.RESULTS_SHEET not in workbook:
        raise ValueError(f"{path.name}: нет листа «{config.RESULTS_SHEET}».")
    sheet = cast(Worksheet, workbook[config.RESULTS_SHEET])
    row_entries = sheet.values
    _validate_header(path, next(row_entries, ()))
    return ResultSheet(path, workbook, sheet, _index_rows(path, row_entries))
