"""Startup banner for OpenHands SDK.

Prints a welcome message with helpful links when the SDK is first imported.
Can be suppressed by setting the OPENHANDS_SUPPRESS_BANNER environment variable.
"""

import os

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


# Not guarded by a lock; worst case in a race is the banner prints twice.
_BANNER_PRINTED = False


def _print_banner(version: str) -> None:
    """Print the OpenHands SDK startup banner to stderr."""
    global _BANNER_PRINTED

    # Check if banner should be suppressed (check this first, before setting flag)
    suppress = os.environ.get("OPENHANDS_SUPPRESS_BANNER", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if suppress:
        return

    if _BANNER_PRINTED:
        return
    _BANNER_PRINTED = True

    console = Console(stderr=True)

    title = Text.from_markup(
        f"[bold blue]OpenHands SDK[/bold blue] [yellow]v{version}[/yellow]"
    )

    body = Text.from_markup(
        "Check out the [bold]SDK Builder Hub[/bold]: "
        "[cyan link=https://sdkbuilders.openhands.dev]sdkbuilders.openhands.dev[/cyan link]\n"  # noqa:E501
        "[dim]- Apply for LLM development credits\n"
        "- Join OpenHands Slack community\n"
        "- Access SDK docs, report bugs, and suggest features[/dim]\n\n"
        "[dim]Set OPENHANDS_SUPPRESS_BANNER=1 to hide this message[/dim]"
    )

    panel = Panel(
        body,
        title=title,
        title_align="left",
        border_style="magenta",
        width=70,
        padding=(0, 1),
    )
    console.print(panel)
