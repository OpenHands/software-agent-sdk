"""Redaction helpers that keep secrets out of exported artifacts.

Used at export boundaries (e.g. trajectory download) where persisted
conversation data leaves the server and must never carry credential
material — neither plaintext nor recoverable Fernet ciphertext.
"""

import json
from pathlib import Path

from openhands.sdk.llm.llm import LLM_SECRET_FIELDS
from openhands.sdk.utils.cipher import FERNET_TOKEN_PREFIX
from openhands.sdk.utils.pydantic_secrets import REDACTED_SECRET_VALUE


# Field names that carry credentials anywhere in a persisted conversation
# payload (base_state.json, meta.json, events). Kept in sync with
# ``LLM_SECRET_FIELDS`` and extended with the settings-level ``llm_api_key``
# alias.
TRAJECTORY_SECRET_FIELDS: frozenset[str] = frozenset(
    (*LLM_SECRET_FIELDS, "llm_api_key")
)


def _is_secret_value(key: object, value: object) -> bool:
    if not isinstance(value, str) or value in ("", REDACTED_SECRET_VALUE):
        return False
    # Any Fernet token in a persisted conversation is ciphertext produced with
    # the server's OH_SECRET_KEY (e.g. custom secrets in
    # ``secret_registry.secret_sources``) and therefore recoverable secret
    # material, regardless of which field carries it.
    if value.startswith(FERNET_TOKEN_PREFIX):
        return True
    return key in TRAJECTORY_SECRET_FIELDS


def redact_secrets_in_obj(obj: object) -> bool:
    """Recursively replace secret-bearing values with the redaction marker.

    Returns ``True`` if anything was changed. Both plaintext and encrypted
    (Fernet) secret material are masked, so the guarantee holds regardless of
    whether ``OH_SECRET_KEY`` was configured when the conversation was
    persisted.
    """
    changed = False
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _is_secret_value(key, value):
                obj[key] = REDACTED_SECRET_VALUE
                changed = True
            elif redact_secrets_in_obj(value):
                changed = True
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            if _is_secret_value(None, item):
                obj[index] = REDACTED_SECRET_VALUE
                changed = True
            elif redact_secrets_in_obj(item):
                changed = True
    return changed


def redacted_file_bytes(path: Path) -> bytes | None:
    """Return redacted bytes for a JSON/JSONL file, or ``None`` if unchanged.

    Persisted conversation files are JSON (``*.json``) or newline-delimited
    JSON (``*.jsonl``/the per-event log files). Anything that does not parse as
    JSON is left untouched (returns ``None``) so the archive is byte-identical
    for non-secret content.
    """
    suffix = path.suffix.lower()
    if suffix not in (".json", ".jsonl"):
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    if suffix == ".jsonl":
        changed = False
        out_lines: list[str] = []
        for line in raw.splitlines(keepends=True):
            stripped = line.strip()
            if not stripped:
                out_lines.append(line)
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                out_lines.append(line)
                continue
            if redact_secrets_in_obj(obj):
                changed = True
            newline = "\n" if line.endswith("\n") else ""
            out_lines.append(json.dumps(obj) + newline)
        return "".join(out_lines).encode("utf-8") if changed else None

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not redact_secrets_in_obj(obj):
        return None
    return json.dumps(obj).encode("utf-8")
