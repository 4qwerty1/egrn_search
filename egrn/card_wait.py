from contextlib import ExitStack, suppress

from playwright.sync_api import Error, JSHandle, Locator, Page

from egrn import config
from egrn.rosreestr import PAGE_STATE, require_session


MODAL = ".realestate-object-modal"
CARD = f"{MODAL} .build-card-wrapper.card"
CARD_TITLE = f"{MODAL} .realestate-object-modal__h1"
RESULTS_CONTAINER = ".main__content.main__content_with-aside"
SPINNER_WRAPPER = ".rros-ui-lib-spinner__wrapper"
SPINNER = f"{RESULTS_CONTAINER} > {SPINNER_WRAPPER}"

OBSERVE_SPINNER = f"""
() => {{
    const content = document.querySelector({RESULTS_CONTAINER!r});
    if (!content) throw new Error('Не найден контейнер выдачи.');
    const state = {{idleSince: null}};
    const isSpinner = node => node instanceof Element && node.matches({SPINNER_WRAPPER!r});
    state.observer = new MutationObserver(records => {{
        for (const record of records) {{
            if ([...record.addedNodes, ...record.removedNodes].some(isSpinner)) {{
                state.idleSince = performance.now();
            }}
        }}
    }});
    state.observer.observe(content, {{childList: true}});
    return state;
}}"""
CARD_STATE = f"""
state => {{
    {PAGE_STATE}
    const error = sessionError();
    if (error) return {{error}};
    if (shown({SPINNER!r}).length) {{
        state.idleSince = null;
        return false;
    }}
    const title = shown({CARD_TITLE!r}).some(node => node.textContent.trim() === 'Сведения об объекте');
    if (title && shown({CARD!r}).length) return {{opened: true}};
    const now = performance.now();
    state.idleSince ??= now;
    return now - state.idleSince >= {config.CARD_GRACE_MS} && {{opened: false}};
}}"""


def _disconnect(state: JSHandle) -> None:
    with suppress(Error):
        state.evaluate("state => state.observer.disconnect()")


def wait_for_card(page: Page, row: Locator, timeout_ms: int) -> bool:
    page.locator(f"{SPINNER}:visible").wait_for(state="hidden", timeout=timeout_ms)
    require_session(page)
    with ExitStack() as handles:
        state = page.evaluate_handle(OBSERVE_SPINNER)
        handles.callback(state.dispose)
        handles.callback(_disconnect, state)
        row.click(timeout=timeout_ms)
        ready = page.wait_for_function(
            CARD_STATE, arg=state, polling=config.CARD_POLL_MS, timeout=timeout_ms + config.CARD_GRACE_MS,
        )
        handles.callback(ready.dispose)
        outcome = ready.json_value()
    error = outcome.get("error")
    if error:
        raise RuntimeError(error)
    return outcome["opened"]
