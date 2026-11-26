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
SUCCESS = "Да"
SPINNER_TIME = "spinnerMs"


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
        self.assertEqual(self.delays, [
            1, 1, 0.5, 1,
            5,
            1, 1, 0.5, 1,
        ])
        self.assertEqual(self.rows(), [
            HEADERS,
            (FIRST_ACCOUNT, first, SUCCESS, PRIVATE_OWNERSHIP, filename(first)),
            ("00102", first, SUCCESS, PRIVATE_OWNERSHIP, filename(first)),
            (FIRST_ACCOUNT, second, SUCCESS, "Муниципальная", filename(second)),
        ])
        for address in first, second:
            png = self.decode_png(filename(address))
            self.assertEqual(png, [320, 180, [248, 248, 248, 255]])

    def test_screenshot_keeps_existing_files(self):
        address = r'улица Первая, 1/2\:*?"<>|.'
        self.write_addresses((101, address))
        (self.root / SCREENSHOTS).mkdir()
        plain = self.root / SCREENSHOTS / "улица Первая, 12.png"
        occupied = self.root / SCREENSHOTS / "улица Первая, 12_00000.png"
        plain.write_bytes(b"first")
        occupied.write_bytes(b"second")

        scenarios = {address: positive(FIRST_NUMBER)}
        self.assertEqual(self.lookup(scenarios)[0], 0)
        saved = self.rows()[1][-1]
        self.assertRegex(saved, r"^улица Первая, 12_[0-9a-f]{5}\.png$")
        self.assertEqual(self.decode_png(saved)[:2], [320, 180])
        self.assertEqual(plain.read_bytes(), b"first")
        self.assertEqual(occupied.read_bytes(), b"second")

    def test_empty_result_then_blank_ownership(self):
        missing, found = "Неизвестный адрес", "Существующий адрес"
        self.write_addresses((101, missing), (102, found))
        scenarios = {missing: returns_objects(), found: positive(FIRST_NUMBER, "")}
        self.assertEqual(self.lookup(scenarios), (0, ""))
        self.assertEqual(self.activity.submissions, [missing, found])
        self.assertEqual(self.delays, [1, 1, 5, 1, 1, 0.5, 1])
        self.assertEqual(self.rows(), [
            HEADERS,
            (FIRST_ACCOUNT, missing, "Адрес не найден", None, None),
            ("102", found, SUCCESS, None, filename(found)),
        ])
        screenshots = self.root / SCREENSHOTS
        names = {path.name for path in screenshots.iterdir()}
        self.assertEqual(names, {filename(found)})

    def test_ownership_field_error_continues(self):
        missing, duplicate, found = "Без поля", "Два поля", "Следующий адрес"
        self.write_addresses((101, missing), (102, duplicate), (103, found))
        scenarios = {
            missing: positive(FIRST_NUMBER, []),
            duplicate: positive("1:2:3:5", [PRIVATE_OWNERSHIP, "Другая"]),
            found: positive("1:2:3:6"),
        }
        self.assertEqual(self.lookup(scenarios)[0], 0)
        self.assertEqual(self.activity.submissions, [missing, duplicate, found])
        message = "В карточке отсутствует или дублируется поле «Форма собственности»."
        self.assertEqual(self.rows(), [
            HEADERS,
            (FIRST_ACCOUNT, missing, message, None, filename(missing)),
            ("102", duplicate, message, None, filename(duplicate)),
            ("103", found, SUCCESS, PRIVATE_OWNERSHIP, filename(found)),
        ])
        self.assertEqual(
            {path.name for path in (self.root / SCREENSHOTS).iterdir()},
            {filename(missing), filename(duplicate), filename(found)},
        )
        self.assertEqual(self.lookup({})[0], 0)
        self.assertEqual(self.opens, 1)

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
        expected = (FIRST_ACCOUNT, first, SUCCESS, PRIVATE_OWNERSHIP, filename(first))
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
        expected = (FIRST_ACCOUNT, FIRST_ADDRESS, SUCCESS, PRIVATE_OWNERSHIP, filename(FIRST_ADDRESS))
        self.assertEqual(self.rows(), [HEADERS, expected])

    def test_legacy_statuses_skip_browser_lookup(self):
        missing = "Неизвестный адрес"
        self.write_addresses((101, FIRST_ADDRESS), (101, missing))
        _write_book(self.excel, HEADERS, [
            (FIRST_ACCOUNT, FIRST_ADDRESS, True),
            (FIRST_ACCOUNT, missing, False),
        ])
        original = self.excel.read_bytes()
        self.assertEqual(self.lookup({})[0], 0)
        self.assertEqual(self.opens, 0)
        self.assertEqual(self.excel.read_bytes(), original)


class CardFallbackTests(LookupCase):
    def test_kosmichesky_address_opens_second_card(self):
        address = "г.Петропавловск-Камчатский проезд Космический д.19 кв.45"
        first, second = "41:00:0000000:3260", "41:01:0010119:10832"
        self.write_addresses((101, address))
        code, stderr = self.lookup({
            address: returns_objects(
                [first, None, {SPINNER_TIME: 379, "noCard": True}],
                [second, PRIVATE_OWNERSHIP, {SPINNER_TIME: 2_257, "cardDelayMs": 13}],
            ),
        })
        self.assertEqual(code, 0, stderr)
        self._assert_clicks(first, second)
        self._assert_retry_delays()
        self.assertEqual(self.activity.opened, [second])
        self.assertEqual(self.rows(), [
            HEADERS,
            (FIRST_ACCOUNT, address, SUCCESS, PRIVATE_OWNERSHIP, filename(address)),
        ])
        self._assert_only_screenshot(filename(address))

    def test_failed_rows_then_card_on_next_page(self):
        self.write_addresses((101, FIRST_ADDRESS))
        numbers = [FIRST_NUMBER, "41:01:1:5", "41:01:1:6", "41:01:1:7"]
        spinner = {SPINNER_TIME: 120}
        unavailable = {**spinner, "noCard": True}
        scenarios = {FIRST_ADDRESS: {
            **returns_objects(
                [numbers[0], None, unavailable],
                [numbers[1], None, unavailable],
                [numbers[2], PRIVATE_OWNERSHIP, {**spinner, "cardDelayMs": 250}],
                [numbers[3], None],
            ),
            "pageSize": 2,
        }}
        self.assertEqual(self.lookup(scenarios), (0, ""))
        self.assertEqual(self.activity.submissions, [FIRST_ADDRESS])
        self._assert_clicks(*numbers[:3])
        self.assertEqual(self.activity.opened, [numbers[2]])
        self._assert_retry_delays()
        saved = filename(FIRST_ADDRESS)
        self.assertEqual(self.rows(), [
            HEADERS,
            (FIRST_ACCOUNT, FIRST_ADDRESS, SUCCESS, PRIVATE_OWNERSHIP, saved),
        ])
        self._assert_only_screenshot(saved)
        self.assertEqual(self.decode_png(saved)[:2], [320, 180])

    def test_exhausted_results_saved_for_each_account(self):
        found = "Следующий адрес"
        self.write_addresses((101, FIRST_ADDRESS), (202, FIRST_ADDRESS), (203, found))
        unavailable = {SPINNER_TIME: 30, "noCard": True}
        code, stderr = self.lookup({
            FIRST_ADDRESS: returns_objects(
                [FIRST_NUMBER, None, unavailable],
                ["41:02:1:5", None, unavailable],
            ),
            found: positive("41:02:1:6"),
        })
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.activity.submissions, [FIRST_ADDRESS, found])
        self._assert_clicks(FIRST_NUMBER, "41:02:1:5", "41:02:1:6")
        status = "Не получилось открыть карточку объекта"
        self.assertEqual(self.rows(), [
            HEADERS,
            (FIRST_ACCOUNT, FIRST_ADDRESS, status, None, None),
            ("202", FIRST_ADDRESS, status, None, None),
            ("203", found, SUCCESS, PRIVATE_OWNERSHIP, filename(found)),
        ])
        self._assert_only_screenshot(filename(found))

    def test_stuck_spinner_does_not_retry(self):
        self.write_addresses((101, FIRST_ADDRESS))
        code, stderr = self.lookup({
            FIRST_ADDRESS: returns_objects(
                [FIRST_NUMBER, None, {SPINNER_TIME: None}],
                ["41:03:1:5", PRIVATE_OWNERSHIP],
            ),
        }, timeout_ms=1_000)
        self.assertEqual(code, 1)
        self.assertIn("Timeout", stderr)
        self._assert_clicks(FIRST_NUMBER)
        self.assertEqual(self.rows(), [HEADERS])

    def _assert_clicks(self, *numbers: str) -> None:
        actual = [click["number"] for click in self.activity.clicks]
        self.assertEqual(actual, list(numbers))

    def _assert_retry_delays(self) -> None:
        retries = self.activity.clicks[1:]
        stops = self.activity.spinner_stops[:len(retries)]
        for stop, retry in zip(stops, retries, strict=True):
            self.assertGreaterEqual(retry["at"] - stop["at"], 1_000)

    def _assert_only_screenshot(self, saved: str) -> None:
        directory = self.root.joinpath(SCREENSHOTS)
        self.assertEqual([path.name for path in directory.iterdir()], [saved])


class WorkbookTests(LookupCase):
    def test_resume_skips_saved_results_and_appends(self):
        saved, missing, new = "Уже сохранён", "Неизвестный адрес", "Новый адрес"
        self.write_addresses((101, saved), (102, missing))
        self.assertEqual(
            self.lookup({
                saved: positive(FIRST_NUMBER),
                missing: returns_objects(),
            }),
            (0, ""),
        )
        screenshot = self.root / SCREENSHOTS / filename(saved)
        original = screenshot.read_bytes()

        self.write_addresses((103, new), (101, saved), (102, missing))
        self.assertEqual(self.lookup({new: returns_objects()}), (0, ""))
        self.assertEqual(self.activity.submissions, [new])
        self.assertEqual(self.delays, [1, 1])
        self.assertEqual(self.rows(), [
            HEADERS,
            (FIRST_ACCOUNT, saved, SUCCESS, PRIVATE_OWNERSHIP, filename(saved)),
            ("102", missing, "Адрес не найден", None, None),
            ("103", new, "Адрес не найден", None, None),
        ])
        self.assertEqual(screenshot.read_bytes(), original)

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
            (HEADERS, [(FIRST_ACCOUNT, FIRST_ADDRESS, 1)], "нужен логический статус поиска"),
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

    def _assert_invalid_book(self, header, body, error):
        _write_book(self.excel, header, body)
        original = self.excel.read_bytes()
        code, stderr = self.lookup({})
        self.assertEqual(code, 1)
        self.assertIn(error, stderr)
        self.assertEqual(self.opens, 0)
        self.assertEqual(self.excel.read_bytes(), original)
