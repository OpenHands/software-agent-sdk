# Live verification: deepseek-v4.1-flash vision + tool-result arrays

Follow-up to the review findings on PR #5524. Run on 2026-10-05 against the
OpenHands proxy (`https://llm-proxy.app.all-hands.dev`) with the managed key
fetched from `https://app.all-hands.dev/api/keys/llm/managed/current`, using the
`openhands/deepseek-v4.1-flash` route.

Not a CI test: it needs live proxy credentials, so it is recorded here as
evidence rather than added to the default suite. The deterministic equivalent
(`test_deepseek_vision_models_keep_images_in_chat_payload`) lives in
`tests/sdk/llm/test_vision_support.py`.

## Finding 1 — image-bearing user message is delivered

`format_messages_for_llm()` now emits list content for the four model forms, and
a real request returned a description of the attached image:

```
vision_is_active: True   force_string_serializer: False
payload has image part: True
RESPONSE: A white circle centered on a red square background.
```

Image: 128x128 PNG, red field with a white circle. Before commit `53adb22` the
same call would have sent only the question text (string serializer drops
`ImageContent`).

## Finding 2 — tool-result content arrays are accepted by the proxy

A two-turn native tool-call round trip where the `tool` message carries an image
as a content array:

```
turn1 tool_calls: [('chatcmpl-tool-9716cc6825d7491b', 'describe_image')]
tool content is list: True
tool has image part: True
RESPONSE: A yellow square outline on a solid blue background.
```

Image: 128x128 PNG, blue field with a yellow rectangle outline. The proxy
accepted the list-format `tool` message and the model saw the image, so the
`!deepseek-v4-flash` / `!deepseek-v4.1-flash` exclusions are compatible with the
serving route for tool results, not just for user messages.

## Finding 3 — the vision inspection tool delivers its image

The finding also claims `vision_inspect.py` "selects the profile but cannot
deliver its image". Ran `VisionInspectExecutor` end-to-end against a real
`Conversation` whose latest user message carried a 128x128 PNG (green field,
white triangle), routing `get_or_create_profile_llm` to the live
`openhands/deepseek-v4.1-flash` LLM:

```
is_error: False
model: openhands/deepseek-v4.1-flash
ANSWER: A white triangle centered on a solid green background.
```

The executor builds a `user` message with `ImageContent` and calls
`vision_llm.generate(...)`, which for this model uses the Chat Completions path
(`uses_responses_api() == False`). The image is serialized as list content and
reaches the model. The deterministic equivalent
(`test_vision_profile_tool_keeps_image_for_deepseek_vision_model`) is in
`tests/sdk/agent/test_non_multimodal_image_input.py`.

## Scope

Only `openhands/deepseek-v4.1-flash` was exercised live. The exclusion rules also
cover `deepseek-v4-flash`, whose LiteLLM-resolved route was not separately
round-tripped here.
