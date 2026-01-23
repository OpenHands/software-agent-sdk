"""
Standalone chat template implementation using Jinja2.

This module provides a lightweight implementation of chat template rendering
that is compatible with HuggingFace transformers but removes the dependency
on the full transformers library.

The implementation follows the same approach as transformers:
- Uses Jinja2 for template rendering
- Loads templates dynamically from tokenizer_config.json
- Supports caching of compiled templates and fetched configs
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import jinja2
from jinja2.ext import loopcontrols
from jinja2.sandbox import ImmutableSandboxedEnvironment


# Cache directory for downloaded tokenizer configs
CACHE_DIR = Path.home() / ".cache" / "chat_templates"


def _get_cache_path(tokenizer_name: str) -> Path:
    """Get the cache path for a tokenizer config."""
    # Create a safe filename from the tokenizer name
    safe_name = hashlib.md5(tokenizer_name.encode()).hexdigest()
    return CACHE_DIR / f"{safe_name}_tokenizer_config.json"


def _fetch_tokenizer_config(
    tokenizer_name: str, use_cache: bool = True
) -> dict[str, Any]:
    """
    Fetch tokenizer_config.json from HuggingFace Hub.

    Args:
        tokenizer_name: The HuggingFace model/tokenizer name
            (e.g., "Qwen/Qwen3-4B-Instruct-2507")
        use_cache: Whether to use cached config if available

    Returns:
        The parsed tokenizer config dictionary
    """
    cache_path = _get_cache_path(tokenizer_name)

    # Try to load from cache
    if use_cache and cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    # Fetch from HuggingFace Hub
    url = f"https://huggingface.co/{tokenizer_name}/raw/main/tokenizer_config.json"

    try:
        request = Request(url, headers={"User-Agent": "chat_template/1.0"})
        with urlopen(request, timeout=30) as response:
            config = json.loads(response.read().decode("utf-8"))
    except URLError as e:
        raise RuntimeError(f"Failed to fetch tokenizer config from {url}: {e}")

    # Cache the config
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(config, f)

    return config


@lru_cache(maxsize=16)
def _compile_jinja_template(chat_template: str) -> jinja2.Template:
    """
    Compile a Jinja2 chat template.

    This matches the transformers implementation with custom tojson filter
    and other utilities.
    """

    def raise_exception(message: str) -> None:
        raise jinja2.exceptions.TemplateError(message)

    def tojson(
        x: Any,
        ensure_ascii: bool = False,
        indent: int | None = None,
        separators: tuple[str, str] | None = None,
        sort_keys: bool = False,
    ) -> str:
        # Match the transformers implementation - no HTML escaping
        return json.dumps(
            x,
            ensure_ascii=ensure_ascii,
            indent=indent,
            separators=separators,
            sort_keys=sort_keys,
        )

    jinja_env = ImmutableSandboxedEnvironment(
        trim_blocks=True,
        lstrip_blocks=True,
        extensions=[loopcontrols],
    )
    jinja_env.filters["tojson"] = tojson
    jinja_env.globals["raise_exception"] = raise_exception

    return jinja_env.from_string(chat_template)


class ChatTemplateRenderer:
    """
    A lightweight chat template renderer compatible with HuggingFace transformers.

    This class can dynamically load templates from HuggingFace Hub or use
    provided templates directly.
    """

    def __init__(
        self,
        tokenizer_name: str | None = None,
        chat_template: str | None = None,
        use_cache: bool = True,
    ):
        """
        Initialize the renderer.

        Args:
            tokenizer_name: HuggingFace tokenizer name to load template from.
                If provided, will fetch tokenizer_config.json from
                HuggingFace Hub.
            chat_template: Direct Jinja2 template string.
                If provided, tokenizer_name is ignored.
            use_cache: Whether to cache fetched tokenizer configs.
        """
        if chat_template is not None:
            self._chat_template = chat_template
        elif tokenizer_name is not None:
            config = _fetch_tokenizer_config(tokenizer_name, use_cache=use_cache)
            self._chat_template = config.get("chat_template")
            if self._chat_template is None:
                raise ValueError(
                    f"No chat_template found in tokenizer config for {tokenizer_name}"
                )
        else:
            raise ValueError("Either tokenizer_name or chat_template must be provided")

        self._compiled_template = _compile_jinja_template(self._chat_template)

    @property
    def chat_template(self) -> str:
        """The raw Jinja2 chat template string."""
        assert self._chat_template is not None
        return self._chat_template

    def apply_chat_template(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        add_generation_prompt: bool = False,
        **kwargs: Any,
    ) -> str:
        """
        Apply the chat template to format messages.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            tools: Optional list of tool definitions for function calling.
            add_generation_prompt: If True, append assistant prompt at the end.
            **kwargs: Additional template variables.

        Returns:
            Formatted string ready for tokenization.
        """
        return self._compiled_template.render(
            messages=messages,
            tools=tools,
            add_generation_prompt=add_generation_prompt,
            **kwargs,
        )


# Convenience function for simple use cases
def apply_chat_template(
    messages: Sequence[dict[str, Any]],
    tokenizer_name: str | None = None,
    chat_template: str | None = None,
    tools: Sequence[dict[str, Any]] | None = None,
    add_generation_prompt: bool = False,
    use_cache: bool = True,
    **kwargs: Any,
) -> str:
    """
    Apply a chat template to format messages.

    This is a convenience function that creates a renderer and applies the
    template. For repeated use with the same tokenizer, prefer using
    ChatTemplateRenderer directly.

    Args:
        messages: List of message dicts with 'role' and 'content' keys.
        tokenizer_name: HuggingFace tokenizer name to load template from.
        chat_template: Direct Jinja2 template string.
            If provided, tokenizer_name is ignored.
        tools: Optional list of tool definitions for function calling.
        add_generation_prompt: If True, append assistant prompt at the end.
        use_cache: Whether to cache fetched tokenizer configs.
        **kwargs: Additional template variables.

    Returns:
        Formatted string ready for tokenization.
    """
    renderer = ChatTemplateRenderer(
        tokenizer_name=tokenizer_name,
        chat_template=chat_template,
        use_cache=use_cache,
    )
    return renderer.apply_chat_template(
        messages=messages,
        tools=tools,
        add_generation_prompt=add_generation_prompt,
        **kwargs,
    )
