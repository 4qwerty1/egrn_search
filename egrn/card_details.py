import re
from collections.abc import Callable
from typing import NamedTuple

from playwright.sync_api import Locator, Page

from egrn import config
from egrn.card_wait import CARD
from egrn.rosreestr import require_session


FIELD = "build-card-wrapper__info__ul__subinfo"


class CardMatch(NamedTuple):
    ownership: str | None
    png: bytes
    error: str | None


class _CardFieldError(RuntimeError):
    """Название поля отсутствует или дублируется."""


def _field_text(card: Locator, label: str) -> str:
    name = card.locator(f"li.{FIELD} > span.{FIELD}__name").filter(
        has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"),
    )
    if name.count() != 1:
        raise _CardFieldError(f"В карточке отсутствует или дублируется поле «{label}».")
    options = name.locator("..").locator(f":scope > div.{FIELD}__options")
    if options.count() != 1:
        raise RuntimeError(f"Не найден единственный контейнер значения поля «{label}».")
    return options.inner_text().strip()


def _capture_card(page: Page, card: Locator, pause: Callable[[float], None]) -> CardMatch:
    ownership = None
    error = None
    try:
        ownership = _field_text(card, "Форма собственности")
    except _CardFieldError as missing:
        error = str(missing)
    require_session(page)
    png = card.screenshot(type="png", animations="disabled", scale="css", timeout=config.DEFAULT_TIMEOUT_MS)
    pause(config.AFTER_SCREENSHOT_DELAY_SECONDS)
    return CardMatch(ownership, png, error)


def read_card(page: Page, number: str, pause: Callable[[float], None]) -> CardMatch:
    pause(config.CARD_ANALYSIS_DELAY_SECONDS)
    card = page.locator(f"{CARD}:visible")
    general_info = card.locator('xpath=.//ul[normalize-space(../*[1]) = "Общая информация"]')
    if _field_text(general_info, "Кадастровый номер") != number:
        raise RuntimeError(f"Кадастровый номер карточки не совпадает с номером результата {number!r}.")
    return _capture_card(page, card, pause)
