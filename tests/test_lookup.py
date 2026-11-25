from contextlib import closing
from functools import partial
from os import replace
from unittest.mock import patch

from openpyxl import Workbook

from lookup_support import (
    PRIVATE_OWNERSHIP,
    SCREENSHOTS,
    SHEET,
    LookupCase,
    filename,
    positive,
    returns_objects,
)


HEADERS = ("Номер ЛС", "Адрес", "Успешен ли поиск", "Форма собственности", "Название файла скриншота")
FIRST_ADDRESS = "Первый"
FIRST_NUMBER = "1:2:3:4"
FIRST_ACCOUNT = "101"


def _replace_unless(source, destination, *, blocked):
    if destination == blocked:
        raise PermissionError("книга недоступна для записи")
    replace(source, destination)


def _write_book(path, header, body):
    with closing(Workbook()) as workbook:
        sheet = workbook.active
        sheet.title = SHEET
        sheet.append(header)
        for row in body:
            sheet.append(row)
        workbook.save(path)


class BrowserLookupTests(LookupCase):
    def test_first_visible_row_and_fresh_counter(self):
        first, second = "улица Первая, 1", "улица Вторая, 2"
        self.write_addresses(
            (101, f" {first}  "), (), (101, first),
            (101, second), ("00102", first),
        )
        scenarios = {
            first: returns_objects(["99:01:2:8", PRIVATE_OWNERSHIP], ["99:01:2:1", "Другая"]),
            second: returns_objects(["99:01:3:9", "  Муниципальная  "], ["99:01:3:1", "Другая"]),
        }
        self.assertEqual(self.lookup(scenarios), (0, ""))
        self.assertEqual(self.activity.submissions, [first, second])
        self.assertEqual(self.activity.opened, ["99:01:2:8", "99:01:3:9"])
        self.assertEqual(self.delays, [5])
        self.assertEqual(self.rows(), [
            HEADERS,
            (FIRST_ACCOUNT, first, True, PRIVATE_OWNERSHIP, filename(first)),
            ("00102", first, True, PRIVATE_OWNERSHIP, filename(first)),
            (FIRST_ACCOUNT, second, True, "Муниципальная", filename(second)),
        ])
        for address in first, second:
            png = self.decode_png(address)
            self.assertEqual(png, [320, 180, [248, 248, 248, 255]])

    def test_empty_result_then_blank_ownership(self):
        missing, found = "Неизвестный адрес", "Существующий адрес"
        self.write_addresses((101, missing), (102, found))
        scenarios = {missing: returns_objects(), found: positive(FIRST_NUMBER, "")}
        self.assertEqual(self.lookup(scenarios), (0, ""))
        self.assertEqual(self.activity.submissions, [missing, found])
        self.assertEqual(self.delays, [5])
        self.assertEqual(self.rows(), [
            HEADERS,
            (FIRST_ACCOUNT, missing, False, None, None),
            ("102", found, True, None, filename(found)),
        ])
        screenshots = self.root / SCREENSHOTS
        names = {path.name for path in screenshots.iterdir()}
        self.assertEqual(names, {filename(found)})

    def test_captcha_after_save_stops_next_search(self):
        first, second = FIRST_ADDRESS, "Второй"
        self.write_addresses((101, first), (102, second))
        code, stderr = self.lookup({
            first: {**positive(FIRST_NUMBER), "captchaAfterClose": True},
            second: positive("1:2:3:5"),
        })
        self.assertEqual(code, 1)
        self.assertIn("CAPTCHA", stderr)
        self.assertIn(second, stderr)
        self.assertEqual(self.activity.submissions, [first])
        expected = (FIRST_ACCOUNT, first, True, PRIVATE_OWNERSHIP, filename(first))
        self.assertEqual(self.rows(), [HEADERS, expected])

    def test_mutated_counter_is_not_fresh(self):
        second = "Без нового счётчика"
        self.write_addresses((101, FIRST_ADDRESS), (102, second))
        scenarios = {FIRST_ADDRESS: positive(FIRST_NUMBER), second: {"reuseCounter": True}}
        code, stderr = self.lookup(scenarios, timeout_ms=1000)
        self.assertEqual(code, 1)
        self.assertIn(second, stderr)
        self.assertIn("Timeout", stderr)
        self.assertEqual(self.activity.submissions, [FIRST_ADDRESS, second])
        expected = (FIRST_ACCOUNT, FIRST_ADDRESS, True, PRIVATE_OWNERSHIP, filename(FIRST_ADDRESS))
        self.assertEqual(self.rows(), [HEADERS, expected])


class WorkbookTests(LookupCase):
    def test_resume_updates_false_and_appends(self):
        saved, retry, new = "Уже сохранён", "Повторная попытка", "Новый адрес"
        self.write_addresses((101, saved), (102, retry))
        self.assertEqual(
            self.lookup({
                saved: positive(FIRST_NUMBER),
                retry: returns_objects(),
            }),
            (0, ""),
        )
        screenshot = self.root / SCREENSHOTS / filename(saved)
        original = screenshot.read_bytes()

        self.write_addresses((103, new), (101, saved), (102, retry))
        self.assertEqual(
            self.lookup({
                retry: positive("1:2:3:5"),
                new: returns_objects(),
            }),
            (0, ""),
        )
        self.assertEqual(self.activity.submissions, [new, retry])
        self.assertEqual(self.delays, [5])
        self.assertEqual(self.rows(), [
            HEADERS,
            (FIRST_ACCOUNT, saved, True, PRIVATE_OWNERSHIP, filename(saved)),
            ("102", retry, True, PRIVATE_OWNERSHIP, filename(retry)),
            ("103", new, False, None, None),
        ])
        self.assertEqual(screenshot.read_bytes(), original)
        self._assert_successes_skipped(saved, retry)

    def test_invalid_workbook_never_opens_browser(self):
        self.write_addresses((101, FIRST_ADDRESS))
        for header, body, error in (
            (HEADERS[1:], [], "заголовки"),
            (
                HEADERS,
                [(FIRST_ACCOUNT, FIRST_ADDRESS, False), (FIRST_ACCOUNT, FIRST_ADDRESS, True)],
                "повтор пары",
            ),
            (HEADERS, [(FIRST_ACCOUNT, None, False)], "нет адреса"),
            (HEADERS, [(None, FIRST_ADDRESS, False)], "нет номера ЛС"),
            (HEADERS, [(FIRST_ACCOUNT, FIRST_ADDRESS, 1)], "не Boolean"),
        ):
            with self.subTest(error=error):
                self._assert_invalid_book(header, body, error)

    def test_empty_input_writes_only_headers(self):
        self.write_addresses((), (" ", "\t"))
        self.assertEqual(self.lookup({}), (0, ""))
        self.assertEqual(self.opens, 0)
        self.assertEqual(self.rows(), [HEADERS])

    def test_failed_replace_keeps_previous_file(self):
        original = self._save_empty_book()
        self.write_addresses((101, FIRST_ADDRESS))
        with patch(
            "results.os.replace",
            side_effect=partial(_replace_unless, blocked=self.excel),
        ):
            code, stderr = self.lookup({FIRST_ADDRESS: positive(FIRST_NUMBER)})
        self.assertEqual(code, 1)
        self.assertIn(FIRST_ADDRESS, stderr)
        self.assertIn("книга недоступна для записи", stderr)
        self.assertEqual(self.excel.read_bytes(), original)
        self.assertEqual(self.rows(), [HEADERS])
        self.assertEqual(list(self.root.glob(".result.xlsx.*")), [])
        screenshots = self.root / SCREENSHOTS
        expected = screenshots / filename(FIRST_ADDRESS)
        self.assertEqual(list(screenshots.iterdir()), [expected])

    def _save_empty_book(self):
        self.write_addresses()
        self.assertEqual(self.lookup({}), (0, ""))
        return self.excel.read_bytes()

    def _assert_successes_skipped(self, saved, retry):
        self.write_addresses((101, saved), (102, retry))
        workbook = self.excel.read_bytes()
        self.assertEqual(self.lookup({}), (0, ""))
        self.assertEqual(self.opens, 2)
        self.assertEqual(self.excel.read_bytes(), workbook)

    def _assert_invalid_book(self, header, body, error):
        _write_book(self.excel, header, body)
        original = self.excel.read_bytes()
        code, stderr = self.lookup({})
        self.assertEqual(code, 1)
        self.assertIn(error, stderr)
        self.assertEqual(self.opens, 0)
        self.assertEqual(self.excel.read_bytes(), original)
