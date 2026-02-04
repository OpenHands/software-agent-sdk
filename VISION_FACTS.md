# Responses API vision example (run results)

**Example script:** `examples/01_standalone_sdk/23_responses_reasoning.py`

**Image input:** `examples/01_standalone_sdk/responses_reasoning_screenshot.png` (encoded as a `data:image/png;base64,...` URL)

**Run command:**

```bash
python examples/01_standalone_sdk/23_responses_reasoning.py
```

**Model:** `gpt-5-mini`

**Result:** The Responses API accepted the image input and returned a description that was written to `VISION_FACTS.md` by the agent run. Below is the assistant output from the conversation:

```
Key elements:
- Large header: "OpenHands Responses API" (prominent, top-left).
- Subtitle: "Generated screenshot placeholder" under the header.
- Blue-outlined code-style box with the text: "Vision request: Describe key elements in this image."
- Green-outlined box showing repo metadata: "Repo: software-agent-sdk", "Branch: fix/responses-image-content", "Captured: 2026-02-04".
- Dark navy background with teal/blue accent borders and monospaced/text-heavy layout.

Summary (1–2 sentences):
This is a dark-themed UI mockup titled "OpenHands Responses API" featuring a blue request box and a green metadata panel that lists the repository, branch, and capture date. The layout uses color-coded bordered panels to separate the vision request from repository details.
```

**Cost:** `EXAMPLE_COST: 0.00870175`
