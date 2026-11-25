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


HEADERS = ("Адрес", "Успешен ли поиск", "Форма собственности", "Название файла скриншота")
FIRST_ADDRESS = "Первый"
FIRST_NUMBER = "1:2:3:4"


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
        self.write_addresses(f" {first}  \n\n{first}\n{second}\n", encoding="utf-8-sig")
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
            (first, True, PRIVATE_OWNERSHIP, filename(first)),
            (second, True, "Муниципальная", filename(second)),
        ])
        for address in first, second:
            png = self.decode_png(address)
            self.assertEqual(png, [320, 180, [248, 248, 248, 255]])

    def test_empty_result_then_blank_ownership(self):
        missing, found = "Неизвестный адрес", "Существующий адрес"
        self.write_addresses(f"{missing}\n{found}\n")
        scenarios = {missing: returns_objects(), found: positive(FIRST_NUMBER, "")}
        self.assertEqual(self.lookup(scenarios), (0, ""))
        self.assertEqual(self.activity.submissions, [missing, found])
        self.assertEqual(self.delays, [5])
        self.assertEqual(self.rows(), [
            HEADERS, (missing, False, None, None), (found, True, None, filename(found)),
        ])
        screenshots = self.root / SCREENSHOTS
        names = {path.name for path in screenshots.iterdir()}
        self.assertEqual(names, {filename(found)})

    def test_captcha_after_save_stops_next_search(self):
        first, second = FIRST_ADDRESS, "Второй"
        self.write_addresses(f"{first}\n{second}\n")
        code, stderr = self.lookup({
            first: {**positive(FIRST_NUMBER), "captchaAfterClose": True},
            second: positive("1:2:3:5"),
        })
        self.assertEqual(code, 1)
        self.assertIn("CAPTCHA", stderr)
        self.assertIn(second, stderr)
        self.assertEqual(self.activity.submissions, [first])
        expected = (first, True, PRIVATE_OWNERSHIP, filename(first))
        self.assertEqual(self.rows(), [HEADERS, expected])

    def test_mutated_counter_is_not_fresh(self):
        second = "Без нового счётчика"
        self.write_addresses(f"{FIRST_ADDRESS}\n{second}\n")
        scenarios = {FIRST_ADDRESS: positive(FIRST_NUMBER), second: {"reuseCounter": True}}
        code, stderr = self.lookup(scenarios, timeout_ms=1000)
        self.assertEqual(code, 1)
        self.assertIn(second, stderr)
        self.assertIn("Timeout", stderr)
        self.assertEqual(self.activity.submissions, [FIRST_ADDRESS, second])
        expected = (FIRST_ADDRESS, True, PRIVATE_OWNERSHIP, filename(FIRST_ADDRESS))
        self.assertEqual(self.rows(), [HEADERS, expected])


class WorkbookTests(LookupCase):
    def test_resume_updates_false_and_appends(self):
        saved, retry, new = "Уже сохранён", "Повторная попытка", "Новый адрес"
        self.write_addresses(f"{saved}\n{retry}\n")
        self.assertEqual(
            self.lookup({
                saved: positive(FIRST_NUMBER),
                retry: returns_objects(),
            }),
            (0, ""),
        )
        screenshot = self.root / SCREENSHOTS / filename(saved)
        original = screenshot.read_bytes()

        self.write_addresses(f"{new}\n{saved}\n{retry}\n")
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
            (saved, True, PRIVATE_OWNERSHIP, filename(saved)),
            (retry, True, PRIVATE_OWNERSHIP, filename(retry)),
            (new, False, None, None),
        ])
        self.assertEqual(screenshot.read_bytes(), original)
        self._assert_successes_skipped(saved, retry)

    def test_invalid_workbook_never_opens_browser(self):
        self.write_addresses(f"{FIRST_ADDRESS}\n")
        for header, body, error in (
            (tuple(reversed(HEADERS)), [], "заголовки"),
            (HEADERS, [(FIRST_ADDRESS, False), (FIRST_ADDRESS, True)], "повтор адреса"),
            (HEADERS, [(None, False)], "нет адреса"),
            (HEADERS, [(FIRST_ADDRESS, 1)], "не Boolean"),
        ):
            with self.subTest(error=error):
                self._assert_invalid_book(header, body, error)

    def test_empty_input_writes_only_headers(self):
        self.write_addresses(" \n\t\n", encoding="utf-8-sig")
        self.assertEqual(self.lookup({}), (0, ""))
        self.assertEqual(self.opens, 0)
        self.assertEqual(self.rows(), [HEADERS])

    def test_failed_replace_keeps_previous_file(self):
        original = self._save_empty_book()
        self.write_addresses(f"{FIRST_ADDRESS}\n")
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
        self.write_addresses("\n")
        self.assertEqual(self.lookup({}), (0, ""))
        return self.excel.read_bytes()

    def _assert_successes_skipped(self, saved, retry):
        self.write_addresses(f"{saved}\n{retry}\n")
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
