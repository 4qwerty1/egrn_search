import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet


SHEET = "Результаты"
HEADERS = ("Адрес", "Успешен ли поиск", "Форма собственности", "Название файла скриншота")
FOUND_COLUMN = 2

type SheetRow = tuple[object, ...]
type ResultFields = tuple[str, bool, str | None, str | None]


class ResultSheet:
    def __init__(self, path: Path, workbook: Workbook, sheet: Worksheet, rows: dict[str, int]) -> None:
        self._path: Path = path
        self._workbook: Workbook = workbook
        self._sheet: Worksheet = sheet
        self._rows: dict[str, int] = rows

    def has_success(self, address: str) -> bool:
        row = self._rows.get(address)
        return row is not None and self._sheet.cell(row, FOUND_COLUMN).value is True

    def save(self, address: str, match: tuple[str, bytes] | None) -> None:
        ownership = None
        filename = None
        if match is not None:
            ownership, png = match
            filename = _store_screenshot(self._path.parent, address, png)

        row = self._rows.get(address)
        if row is None:
            row = self._sheet.max_row + 1
        fields = (address, match is not None, ownership, filename)
        self._write_row(row, fields)
        _publish(self._path, self._workbook.save)

    def close(self) -> None:
        self._workbook.close()

    def _write_row(self, row: int, fields: ResultFields) -> None:
        for column, entry in enumerate(fields, start=1):
            cell = cast(Cell, self._sheet.cell(row, column))
            cell.value = entry
            if isinstance(entry, str):
                cell.data_type = "s"  # Внешний текст, в том числе начинающийся с «=», не формула.


def _publish(path: Path, write: Callable[[Path], object]) -> None:
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
    with ExitStack() as cleanup:
        cleanup.callback(temporary.unlink, missing_ok=True)
        write(temporary)
        os.replace(temporary, path)
        cleanup.pop_all()


def _store_screenshot(root: Path, address: str, png: bytes) -> str:
    digest = sha256(address.encode("utf-8")).hexdigest()
    filename = f"{digest}.png"
    screenshots = root / "screenshots"
    screenshots.mkdir(exist_ok=True)
    _publish(screenshots / filename, lambda temporary: temporary.write_bytes(png))
    return filename


def _validate_header(path: Path, header: SheetRow) -> None:
    width = len(HEADERS)
    overflow = header[width:]
    has_extra_cells = any(entry is not None for entry in overflow)
    if header[:width] != HEADERS or has_extra_cells:
        raise ValueError(f"{path.name}: неверные заголовки листа «{SHEET}».")


def _check_address(path: Path, number: int, address: object, rows: dict[str, int]) -> str:
    if not isinstance(address, str) or not address.strip():
        raise ValueError(f"{path.name}: нет адреса в строке {number}.")
    if address in rows:
        raise ValueError(f"{path.name}: повтор адреса {address!r} в строке {number}.")
    return address


def _index_rows(path: Path, row_entries: Iterator[SheetRow]) -> dict[str, int]:
    rows: dict[str, int] = {}
    for number, columns in enumerate(row_entries, start=2):
        if all(entry is None for entry in columns):
            continue
        address, found = columns[:FOUND_COLUMN]
        address = _check_address(path, number, address, rows)
        if found is not True and found is not False:
            raise ValueError(f"{path.name}: в строке {number} флаг поиска не Boolean.")
        rows[address] = number
    return rows


def load_results(path: Path) -> ResultSheet:
    if not path.exists():
        workbook = Workbook()
        sheet = cast(Worksheet, workbook.active)
        sheet.title = SHEET
        sheet.append(HEADERS)
        _publish(path, workbook.save)
        return ResultSheet(path, workbook, sheet, {})

    workbook = load_workbook(path)
    if SHEET not in workbook:
        raise ValueError(f"{path.name}: нет листа «{SHEET}».")
    sheet = cast(Worksheet, workbook[SHEET])
    row_entries = sheet.values
    _validate_header(path, next(row_entries, ()))
    return ResultSheet(path, workbook, sheet, _index_rows(path, row_entries))
