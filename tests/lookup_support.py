import base64
import contextlib
import io
import json
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple

from openpyxl import load_workbook
from playwright import sync_api

from main import run


URL = "https://lk.rosreestr.ru/eservices/real-estate-objects-online"
SHEET = "Результаты"
SCREENSHOTS = "screenshots"
PRIVATE_OWNERSHIP = "Частная"
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


def filename(address: str) -> str:
    digest = sha256(address.encode()).hexdigest()
    return f"{digest}.png"


def returns_objects(*entries):
    return {"objects": list(entries)}


def positive(number, ownership=PRIVATE_OWNERSHIP):
    return returns_objects([number, ownership])


def _intercept(route: sync_api.Route, *, html: str) -> None:
    request = route.request
    if request.url == URL and request.resource_type == "document":
        route.fulfill(content_type="text/html; charset=utf-8", body=html)
    else:
        route.abort()


class SearchActivity(NamedTuple):
    submissions: list[str]
    opened: list[str]


class LookupCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        playwright = sync_api.sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        cls.browser = playwright.chromium.launch(headless=True)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.excel = self.root / "result.xlsx"
        self.opens = 0

    def write_addresses(self, text: str, *, encoding: str = "utf-8") -> None:
        (self.root / "addresses.txt").write_text(text, encoding=encoding)

    @contextlib.contextmanager
    def open_page(self, scenarios):
        self.opens += 1
        html = DOCUMENT.replace("__SCENARIOS__", json.dumps(scenarios))
        with contextlib.closing(self.browser.new_context(service_workers="block")) as context:
            context.route("**/*", lambda route: _intercept(route, html=html))
            page = context.new_page()
            try:
                yield page
            finally:
                activity = page.evaluate("[window.submissions, window.opened]")
                self.activity = SearchActivity(*activity)

    def lookup(self, scenarios, *, timeout_ms=3_000):
        self.delays = []
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = run(
                self.root,
                open_page=lambda: self.open_page(scenarios),
                pause=self.delays.append,
                timeout_ms=timeout_ms,
            )
        return code, stderr.getvalue()

    def rows(self):
        with contextlib.closing(load_workbook(self.excel, read_only=True)) as book:
            return list(book[SHEET].values)

    def decode_png(self, address):
        png = (self.root / SCREENSHOTS / filename(address)).read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        with contextlib.closing(self.browser.new_page()) as page:
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
