"""Close a tab that is not focused, then list the tabs at once.

A `BrowserToolExecutor` (the browser tool set's executor, called the way the
agent's tool calls reach it) opens the second page of the local site in a new
tab, switches back to the first tab, closes the new one and lists the tabs
immediately, five times. Reports how many of those lists still showed the
closed tab, and whether every close answered "Closed tab".
"""

import json

from S15.site import install_playwright_chromium, serve
from sdk_verify import emit

from openhands.tools.browser_use import (
    BrowserCloseTabAction,
    BrowserListTabsAction,
    BrowserNavigateAction,
    BrowserSwitchTabAction,
)
from openhands.tools.browser_use.impl import BrowserToolExecutor


ROUNDS = 5

install_playwright_chromium()
base = serve()
executor = BrowserToolExecutor(headless=True)


def tab_ids() -> list[str]:
    return [t["tab_id"] for t in json.loads(executor(BrowserListTabsAction()).text)]


stale = 0
closes: list[str] = []
setup_ok = True
try:
    executor(BrowserNavigateAction(url=f"{base}/index.html"))
    for _ in range(ROUNDS):
        executor(BrowserNavigateAction(url=f"{base}/page2.html", new_tab=True))
        before = tab_ids()
        if len(before) != 2:
            setup_ok = False
            break
        executor(BrowserSwitchTabAction(tab_id=before[0]))
        closes.append(executor(BrowserCloseTabAction(tab_id=before[1])).text)
        if tab_ids() != [before[0]]:
            stale += 1
finally:
    executor.close()

emit(
    {
        "ok": True,
        "rounds": ROUNDS,
        "setup_ok": setup_ok,
        "all_closed": len(closes) == ROUNDS
        and all(c.startswith("Closed tab") for c in closes),
        "stale_lists": stale,
    }
)
