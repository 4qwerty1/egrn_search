import base64
import io
import json
import unittest
from contextlib import closing, contextmanager, redirect_stderr
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from playwright.sync_api import sync_playwright

from main import run


URL = "https://lk.rosreestr.ru/eservices/real-estate-objects-online"
HEADERS = ("Адрес", "Успешен ли поиск", "Форма собственности", "Название файла скриншота")
SHEET = "Результаты"
DOCUMENT = """<!doctype html>
<meta charset="utf-8">
<style>
    body { line-height: 24px; }
    h2 { margin: 0; line-height: 32px; }
    ul { margin: 0; }
    .realestate-object-modal { background: #ddd; padding: 8px; }
    .build-card-wrapper.card { width: 320px; height: 180px; background: rgb(248, 248, 248); }
</style>
<a href="/my-applications" hidden>Мои заявки</a>
<a href="/my-applications">Мои заявки</a>
<div>Поиск по адресу и кадастровому номеру</div>
<div>Выберите значение из справочника</div>
<label>Адрес или кадастровый номер <input id="query"></label>
<button type="button" id="realestateobjects-search" disabled>Найти</button>
<div id="results"></div>
<div id="details"></div>
<script>
const scenarios = __SCENARIOS__;
window.submissions = [];
window.opened = [];
const query = document.querySelector('#query');
const search = document.querySelector('#realestateobjects-search');
const results = document.querySelector('#results');
const details = document.querySelector('#details');
const counterClass = 'realestateobjects-wrapper__results__total-count';
const noResults = 'По заданным критериям поиска объекты не найдены';
const infoClass = 'build-card-wrapper__info__ul__subinfo';

query.addEventListener('input', () => { search.disabled = !query.value; });

function showCard(number, ownership, captcha) {
    window.opened.push(number);
    details.innerHTML = `
        <section class="realestate-object-modal">
            <h2>Сведения об объекте</h2>
            <div class="build-card-wrapper card"><ul></ul></div>
            <button class="realestate-object-modal__btn">ВЕРНУТЬСЯ К РЕЗУЛЬТАТАМ ПОИСКА</button>
        </section>`;
    for (const [label, value] of [['Кадастровый номер', number], ['Форма собственности', ownership]]) {
        const row = document.createElement('li');
        row.className = infoClass;
        row.innerHTML = `<span class="${infoClass}__name"></span><div class="${infoClass}__options"></div>`;
        row.firstElementChild.textContent = label;
        row.lastElementChild.textContent = value;
        details.querySelector('ul').append(row);
    }
    details.querySelector('button').addEventListener('click', () => {
        details.replaceChildren();
        if (captcha) {
            const input = document.createElement('input');
            input.id = 'captcha';
            document.body.append(input);
        }
    });
}

function publishResults(objects, captcha) {
    const counter = document.createElement('span');
    counter.className = counterClass;
    counter.textContent = `Найдено результатов: ${objects.length}`;
    results.replaceChildren(counter);
    if (!objects.length) {
        const message = document.createElement('p');
        message.textContent = noResults;
        results.append(message);
        return;
    }
    results.insertAdjacentHTML('beforeend', `
        <div role="row" class="rros-ui-lib-table__head-row"><div role="cell">Кадастровый номер</div></div>
        <div role="row" class="rros-ui-lib-table__row" hidden><div role="cell">Скрытая строка</div></div>`);
    for (const [number, ownership] of objects) {
        const row = document.createElement('div');
        row.setAttribute('role', 'row');
        row.className = 'rros-ui-lib-table__row';
        row.innerHTML = '<div role="cell"></div><div role="cell">Адрес объекта</div>';
        row.firstElementChild.textContent = number;
        row.addEventListener('click', () => showCard(number, ownership, captcha));
        results.append(row);
    }
}

search.addEventListener('click', () => {
    const address = query.value;
    window.submissions.push(address);
    const scenario = scenarios[address];
    if (!scenario) return;
    // Пока запрос выполняется, видны предыдущие счётчик и строки.
    setTimeout(() => {
        if (scenario.reuseCounter) {
            const old = results.querySelector('.' + counterClass);
            old.textContent = 'Найдено результатов: 0';
            results.replaceChildren(old, document.createElement('p'));
            results.lastElementChild.textContent = noResults;
        } else {
            publishResults(scenario.objects, scenario.captchaAfterClose);
        }
    }, 150);
});
</script>
"""


def filename(address):
    return sha256(address.encode("utf-8")).hexdigest() + ".png"


def positive(number, ownership="Частная"):
    return {"objects": [[number, ownership]]}


class LookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        cls.browser = playwright.chromium.launch(headless=True)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.excel = self.root / "result.xlsx"
        self.addresses = self.root / "addresses.txt"
        self.opens = 0

    @contextmanager
    def open_page(self, scenarios):
        self.opens += 1
        html = DOCUMENT.replace("__SCENARIOS__", json.dumps(scenarios))
        with closing(self.browser.new_context(service_workers="block")) as context:
            def intercept(route):
                request = route.request
                if request.url == URL and request.resource_type == "document":
                    route.fulfill(content_type="text/html; charset=utf-8", body=html)
                else:
                    route.abort()

            context.route("**/*", intercept)
            page = context.new_page()
            try:
                yield page
            finally:
                self.submissions, self.opened = page.evaluate("[window.submissions, window.opened]")

    def lookup(self, scenarios, *, timeout_ms=3_000):
        self.delays = []
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = run(
                self.root,
                open_page=lambda: self.open_page(scenarios),
                pause=self.delays.append,
                timeout_ms=timeout_ms,
            )
        return code, stderr.getvalue()

    def rows(self):
        with closing(load_workbook(self.excel, read_only=True)) as book:
            return list(book[SHEET].values)

    def decode_png(self, address):
        png = (self.root / "screenshots" / filename(address)).read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        with closing(self.browser.new_page()) as page:
            return page.evaluate(
                """async data => {
                    const bytes = Uint8Array.from(atob(data), character => character.charCodeAt(0));
                    const image = await createImageBitmap(new Blob([bytes], {type: 'image/png'}));
                    const {width, height} = image;
                    const canvas = new OffscreenCanvas(width, height).getContext('2d');
                    canvas.drawImage(image, 0, 0);
                    return [width, height, Array.from(canvas.getImageData(width - 1, height - 1, 1, 1).data)];
                }""",
                base64.b64encode(png).decode("ascii"),
            )

    def test_first_visible_result_and_fresh_counter_with_same_text(self):
        first, second = "улица Первая, 1", "улица Вторая, 2"
        self.addresses.write_text(f" {first}  \n\n{first}\n{second}\n", encoding="utf-8-sig")
        scenarios = {
            first: {"objects": [["99:01:2:8", "Частная"], ["99:01:2:1", "Другая"]]},
            second: {"objects": [["99:01:3:9", "  Муниципальная  "], ["99:01:3:1", "Другая"]]},
        }
        self.assertEqual(self.lookup(scenarios), (0, ""))
        self.assertEqual(self.submissions, [first, second])
        self.assertEqual(self.opened, ["99:01:2:8", "99:01:3:9"])
        self.assertEqual(self.delays, [5])
        self.assertEqual(self.rows(), [
            HEADERS,
            (first, True, "Частная", filename(first)),
            (second, True, "Муниципальная", filename(second)),
        ])
        for address in first, second:
            self.assertEqual(self.decode_png(address), [320, 180, [248, 248, 248, 255]])

    def test_confirmed_empty_result_then_open_card_with_empty_ownership(self):
        missing, found = "Неизвестный адрес", "Существующий адрес"
        self.addresses.write_text(f"{missing}\n{found}\n", encoding="utf-8")
        self.assertEqual(self.lookup({missing: {"objects": []}, found: positive("1:2:3:4", "")}), (0, ""))
        self.assertEqual(self.submissions, [missing, found])
        self.assertEqual(self.delays, [5])
        self.assertEqual(self.rows(), [
            HEADERS, (missing, False, None, None), (found, True, None, filename(found)),
        ])
        self.assertEqual({path.name for path in (self.root / "screenshots").iterdir()}, {filename(found)})

    def test_captcha_after_first_save_prevents_next_submission(self):
        first, second = "Первый", "Второй"
        self.addresses.write_text(f"{first}\n{second}\n", encoding="utf-8")
        code, stderr = self.lookup({
            first: {**positive("1:2:3:4"), "captchaAfterClose": True},
            second: positive("1:2:3:5"),
        })
        self.assertEqual(code, 1)
        self.assertIn("CAPTCHA", stderr)
        self.assertIn(second, stderr)
        self.assertEqual(self.submissions, [first])
        self.assertEqual(self.rows(), [HEADERS, (first, True, "Частная", filename(first))])

    def test_mutating_previous_counter_is_not_a_fresh_empty_result(self):
        first, second = "Первый", "Без нового счётчика"
        self.addresses.write_text(f"{first}\n{second}\n", encoding="utf-8")
        code, stderr = self.lookup({first: positive("1:2:3:4"), second: {"reuseCounter": True}}, timeout_ms=1000)
        self.assertEqual(code, 1)
        self.assertIn(second, stderr)
        self.assertIn("Timeout", stderr)
        self.assertEqual(self.submissions, [first, second])
        self.assertEqual(self.rows(), [HEADERS, (first, True, "Частная", filename(first))])

    def test_resumes_false_row_in_place_and_appends_new_address(self):
        saved, retry, new = "Уже сохранён", "Повторная попытка", "Новый адрес"
        self.addresses.write_text(f"{saved}\n{retry}\n", encoding="utf-8")
        self.assertEqual(self.lookup({saved: positive("1:2:3:4"), retry: {"objects": []}}), (0, ""))
        screenshot = self.root / "screenshots" / filename(saved)
        original = screenshot.read_bytes()

        self.addresses.write_text(f"{new}\n{saved}\n{retry}\n", encoding="utf-8")
        self.assertEqual(self.lookup({retry: positive("1:2:3:5"), new: {"objects": []}}), (0, ""))
        self.assertEqual(self.submissions, [new, retry])
        self.assertEqual(self.delays, [5])
        self.assertEqual(self.rows(), [
            HEADERS,
            (saved, True, "Частная", filename(saved)),
            (retry, True, "Частная", filename(retry)),
            (new, False, None, None),
        ])
        self.assertEqual(screenshot.read_bytes(), original)

        self.addresses.write_text(f"{saved}\n{retry}\n", encoding="utf-8")
        workbook = self.excel.read_bytes()
        self.assertEqual(self.lookup({}), (0, ""))
        self.assertEqual(self.opens, 2)
        self.assertEqual(self.excel.read_bytes(), workbook)

    def test_invalid_workbook_never_opens_browser_or_replaces_file(self):
        self.addresses.write_text("Первый\n", encoding="utf-8")
        for header, body, error in (
            (HEADERS[::-1], [], "заголовки"),
            (HEADERS, [("Первый", False), ("Первый", True)], "повтор адреса"),
            (HEADERS, [(None, False)], "нет адреса"),
            (HEADERS, [("Первый", 1)], "не Boolean"),
        ):
            with self.subTest(error=error), closing(Workbook()) as workbook:
                sheet = workbook.active
                sheet.title = SHEET
                sheet.append(header)
                for row in body:
                    sheet.append(row)
                workbook.save(self.excel)
                original = self.excel.read_bytes()
                code, stderr = self.lookup({})
                self.assertEqual(code, 1)
                self.assertIn(error, stderr)
                self.assertEqual(self.opens, 0)
                self.assertEqual(self.excel.read_bytes(), original)

    def test_empty_input_creates_workbook_without_browser(self):
        self.addresses.write_text(" \n\t\n", encoding="utf-8-sig")
        self.assertEqual(self.lookup({}), (0, ""))
        self.assertEqual(self.opens, 0)
        self.assertEqual(self.rows(), [HEADERS])

    def test_failed_workbook_replace_keeps_previous_file_and_cleans_temp(self):
        first = "Первый"
        self.addresses.write_text("\n", encoding="utf-8")
        self.assertEqual(self.lookup({}), (0, ""))
        original = self.excel.read_bytes()
        self.addresses.write_text(f"{first}\n", encoding="utf-8")
        from os import replace

        def fail_book(source, destination):
            if destination == self.excel:
                raise PermissionError("книга недоступна для записи")
            replace(source, destination)

        with patch("main.os.replace", side_effect=fail_book):
            code, stderr = self.lookup({first: positive("1:2:3:4")})
        self.assertEqual(code, 1)
        self.assertIn(first, stderr)
        self.assertIn("книга недоступна для записи", stderr)
        self.assertEqual(self.excel.read_bytes(), original)
        self.assertEqual(self.rows(), [HEADERS])
        self.assertEqual(list(self.root.glob(".result.xlsx.*")), [])
        self.assertEqual(list((self.root / "screenshots").iterdir()), [self.root / "screenshots" / filename(first)])
