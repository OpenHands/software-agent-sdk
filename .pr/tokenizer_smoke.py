"""Check optional Transformers loading using a local, network-free tokenizer.

uv run python .pr/tokenizer_smoke.py
uv run --isolated --frozen --with transformers python .pr/tokenizer_smoke.py
"""

import importlib.util
from importlib.metadata import version
from tempfile import TemporaryDirectory

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm._tokenizer import (
    count_tokenized_output,
    load_chat_template_tokenizer,
)


def main():
    messages = [Message(role="user", content=[TextContent(text="hello world")])]
    if importlib.util.find_spec("transformers") is None:
        assert load_chat_template_tokenizer("not-a-real-model") is None
        assert LLM(model="gpt-4o").get_token_count(messages) > 0
        print("PASS: SDK imports and counts tokens without Transformers")
        return

    # These dependencies are deliberately optional, even in this smoke script.
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import PreTrainedTokenizerFast

    backend = Tokenizer(
        WordLevel({"[UNK]": 0, "hello": 1, "world": 2}, unk_token="[UNK]")
    )
    backend.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]")
    tokenizer.chat_template = (
        "{% for message in messages %}{{ message['content'] }} {% endfor %}"
    )
    with TemporaryDirectory(prefix="sdk-tokenizer-smoke-") as directory:
        tokenizer.save_pretrained(directory)
        loaded = load_chat_template_tokenizer(directory)
        assert loaded is not None
        tokenized = loaded.apply_chat_template(
            [{"role": "user", "content": "hello world"}], tokenize=True
        )
        assert count_tokenized_output(tokenized, loaded) == 2
        llm = LLM(model="gpt-4o", custom_tokenizer=directory)
        assert llm.get_token_count(messages) == 2
    print(f"PASS: Transformers {version('transformers')}, chat-template count = 2")


if __name__ == "__main__":
    main()
