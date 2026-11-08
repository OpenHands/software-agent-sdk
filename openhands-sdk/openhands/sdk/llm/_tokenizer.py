import importlib
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


@runtime_checkable
class ChatTemplateTokenizer(Protocol):
    def apply_chat_template(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> object: ...


@runtime_checkable
class _TokenEncoder(Protocol):
    def encode(self, text: str) -> object: ...


@runtime_checkable
class _ShapedTokens(Protocol):
    @property
    def shape(self) -> Sequence[int]: ...


@runtime_checkable
class _TokenIds(Protocol):
    @property
    def ids(self) -> Sequence[int]: ...


@runtime_checkable
class _TokenMapping(Protocol):
    def get(self, key: str, /) -> object: ...


@runtime_checkable
class _BatchEncodings(Protocol):
    @property
    def encodings(self) -> Sequence[object] | None: ...


@runtime_checkable
class _TokenizerFactory(Protocol):
    def from_pretrained(self, identifier: str) -> object: ...


def chat_template_tokenizer(value: object) -> ChatTemplateTokenizer | None:
    if isinstance(value, dict):
        value = value.get("tokenizer")
    return value if isinstance(value, ChatTemplateTokenizer) else None


def count_tokenized_output(tokenized: object, tokenizer: object) -> int:
    if isinstance(tokenized, str):
        if not isinstance(tokenizer, _TokenEncoder):
            raise TypeError("Tokenizer cannot encode a rendered chat template")
        return count_tokenized_output(tokenizer.encode(tokenized), tokenizer)
    if isinstance(tokenized, _ShapedTokens) and len(tokenized.shape) > 0:
        return int(tokenized.shape[-1])
    if isinstance(tokenized, _TokenIds):
        return len(tokenized.ids)
    if isinstance(tokenized, dict) and "input_ids" in tokenized:
        return count_tokenized_output(tokenized["input_ids"], tokenizer)
    if isinstance(tokenized, _TokenMapping) and callable(tokenized.get):
        input_ids = tokenized.get("input_ids")
        if input_ids is not None:
            return count_tokenized_output(input_ids, tokenizer)
    if isinstance(tokenized, _BatchEncodings) and tokenized.encodings:
        return count_tokenized_output(tokenized.encodings[0], tokenizer)
    if isinstance(tokenized, Sequence):
        if tokenized and isinstance(tokenized[0], _TokenIds):
            return count_tokenized_output(tokenized[0], tokenizer)
        if tokenized and isinstance(tokenized[0], Sequence):
            return len(tokenized[0])
        return len(tokenized)
    raise TypeError(f"Unsupported tokenized output: {type(tokenized).__name__}")


def load_chat_template_tokenizer(identifier: str) -> ChatTemplateTokenizer | None:
    try:
        factory: object = importlib.import_module("transformers").AutoTokenizer
    except (ModuleNotFoundError, AttributeError):
        return None
    except Exception:
        logger.debug("Unable to import transformers", exc_info=True)
        return None

    if not isinstance(factory, _TokenizerFactory):
        return None
    try:
        return chat_template_tokenizer(factory.from_pretrained(identifier))
    except Exception:
        logger.debug(
            "Unable to load chat-template tokenizer for %s",
            identifier,
            exc_info=True,
        )
        return None
