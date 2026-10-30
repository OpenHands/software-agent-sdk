"""Send an explicitly classified SDK request through an admission gateway."""

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import SecretStr

from openhands.sdk import LLM, Message


def admission_llm(
    config: dict[str, Any], gateway_root: str, *, background: bool, api_key: str
) -> LLM:
    """Retain a working LLM configuration and change its transport destination."""
    if (
        not isinstance(gateway_root, str)
        or not gateway_root
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in gateway_root)
        or "\\" in gateway_root
    ):
        raise ValueError("Use a gateway root without whitespace or control characters.")
    try:
        url = urlsplit(gateway_root)
        port = url.port
    except ValueError as exc:
        raise ValueError("Invalid gateway root or TCP port.") from exc
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or "?" in gateway_root
        or "#" in gateway_root
        or url.username is not None
        or url.password is not None
        or url.netloc.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("Use an HTTP(S) gateway root with a valid host and port.")
    path = url.path.rstrip("/")
    if (
        "%" in url.path
        or "//" in url.path
        or any(part in {".", ".."} for part in url.path.split("/"))
        or path.endswith("/v1")
    ):
        raise ValueError("Use an unambiguous gateway root without an API /v1 suffix.")
    route = "/background/v1" if background else "/v1"
    return LLM.model_validate(
        {
            **config,
            "base_url": urlunsplit((url.scheme, url.netloc, path + route, "", "")),
            "api_key": SecretStr(api_key),
            "num_retries": 0,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("llm_config", type=Path)
    parser.add_argument("messages", type=Path)
    parser.add_argument("--gateway-root", default="http://127.0.0.1:18100")
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args()
    llm = admission_llm(
        json.loads(args.llm_config.read_text(encoding="utf-8")),
        args.gateway_root,
        background=args.background,
        api_key=os.environ.get("ADAPTIVE_API_KEY", "local"),
    )
    messages = [
        Message.model_validate(row)
        for row in json.loads(args.messages.read_text(encoding="utf-8"))
    ]
    if not messages or messages[0].role != "system":
        raise ValueError("The first message must have role 'system'.")
    response = llm.completion(messages=messages)
    print(response.message.model_dump_json())
    print(f"EXAMPLE_COST: {response.metrics.accumulated_cost}")


if __name__ == "__main__":
    main()
