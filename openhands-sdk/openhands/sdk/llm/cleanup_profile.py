"""Cleanup profile: repair an agent's outward text before a human reads it.

An agent's reasoning can be sound while the *surface* of a message is broken —
mojibake from mis-encoded emoji, stray control characters, or an inconsistent
format for the target channel. The big reasoning model should not have to police
its own output every turn.

This module runs the agent's outward text through a small, saved LLM profile —
resolved by convention under the name ``cleanup`` (:data:`CLEANUP_PROFILE_NAME`) —
and returns the repaired text. It mirrors the ``ask_oracle`` pattern: no agent
setting and no wiring; a caller saves a profile named ``cleanup`` and calls
:func:`clean_outward_text`.

The pass is deliberately narrow:

- It runs on outward, human-facing text only. Callers must not use it on internal
  agent or tool messages.
- It is stateless: only the cleanup system prompt plus the draft are sent, with
  no conversation history and no tools. The active conversation LLM is never
  switched.
- It repairs the surface only. The prompt forbids adding facts, links, or
  promises; it must not change meaning.
- It fails open. If no ``cleanup`` profile exists, or the call errors, the
  original text is returned unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.message import Message, TextContent
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.utils.cipher import Cipher


logger = get_logger(__name__)

# The cleanup model is a saved LLM profile resolved by convention under this
# name. Save a profile named "cleanup" (e.g. via LLMProfileStore.save("cleanup",
# llm)) and callers on the outward path will consult it. No agent setting or
# wiring is required.
CLEANUP_PROFILE_NAME: Final[str] = "cleanup"

_CLEANUP_SYSTEM_PROMPT = """\
You repair the surface of a message that an AI agent is about to send to a \
human. Fix only the presentation.

Rules:
- Fix mojibake, broken encoding, and stray control characters.
- Keep the meaning exactly. Do not add facts, links, numbers, or promises.
- Do not remove real content. Do not answer or continue the message.
- Keep the author's tone and any intentional formatting.
- Return only the repaired message text, with nothing added around it."""

_CLEANUP_USER_PROMPT_TEMPLATE = """\
Repair this message and return only the repaired text:

{text}"""


def clean_outward_text(text: str, *, cipher: Cipher | None = None) -> str:
    """Return ``text`` repaired by the ``cleanup`` LLM profile, or unchanged.

    Resolves the saved profile named :data:`CLEANUP_PROFILE_NAME` from the
    default :class:`LLMProfileStore` and runs a single stateless completion to
    repair the message surface. This is fail-open: if the profile is missing or
    the call fails for any reason, the original ``text`` is returned unchanged so
    a cleanup problem can never block or corrupt an outward message.

    Args:
        text: The agent's outward, human-facing draft. Do not pass internal
            agent or tool messages.
        cipher: Optional cipher for decrypting the profile's secrets at rest.

    Returns:
        The repaired text, or the original ``text`` when cleanup is unavailable
        or fails, or when ``text`` is empty.
    """
    if not text.strip():
        return text

    try:
        cleanup_llm = LLMProfileStore().load(CLEANUP_PROFILE_NAME, cipher=cipher)
    except FileNotFoundError:
        # No cleanup profile configured: feature is simply off.
        return text
    except Exception as exc:
        logger.warning("Cleanup profile could not be loaded: %s", exc)
        return text

    messages = [
        Message(role="system", content=[TextContent(text=_CLEANUP_SYSTEM_PROMPT)]),
        Message(
            role="user",
            content=[TextContent(text=_CLEANUP_USER_PROMPT_TEMPLATE.format(text=text))],
        ),
    ]

    # Imported lazily: ``agent.utils`` imports from ``openhands.sdk.llm``, so a
    # module-level import here would create a circular import at package init.
    from openhands.sdk.agent.utils import make_llm_completion

    try:
        response = make_llm_completion(cleanup_llm, messages)
    except Exception as exc:
        logger.warning("Cleanup profile call failed; sending original text: %s", exc)
        return text

    cleaned = "".join(
        content.text
        for content in response.message.content
        if isinstance(content, TextContent)
    ).strip()

    # An empty reply means the cleanup model gave us nothing usable; keep the
    # original rather than sending a blank message.
    return cleaned or text
