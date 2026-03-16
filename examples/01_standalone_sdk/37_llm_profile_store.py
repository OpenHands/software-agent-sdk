"""Example: Using LLMProfileStore to save and reuse LLM configurations.

LLMProfileStore persists LLM configurations as JSON files, so you can define
a profile once and reload it across sessions without repeating setup code.
"""

import os
import tempfile

from pydantic import SecretStr

from openhands.sdk import LLM, LLMProfileStore


# Use a temporary directory so this example doesn't pollute your home folder.
# In real usage you can omit base_dir to use the default (~/.openhands/profiles).
store = LLMProfileStore(base_dir=tempfile.mkdtemp())


# 1. Create two LLM profiles with different usage

api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
base_url = os.getenv("LLM_BASE_URL")
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")

fast_llm = LLM(
    usage_id="fast",
    model=model,
    api_key=SecretStr(api_key),
    base_url=base_url,
    temperature=0.0,
)

creative_llm = LLM(
    usage_id="creative",
    model=model,
    api_key=SecretStr(api_key),
    base_url=base_url,
    temperature=0.9,
)

# 2. Save profiles

# Note that secrets are excluded by default for safety.
store.save("fast", fast_llm)
store.save("creative", creative_llm)

# To persist the API key as well, pass `include_secrets=True`:
# store.save("fast", fast_llm, include_secrets=True)

# 3. List available persisted profiles

print(f"Stored profiles: {store.list()}")

# 4. Load a profile

loaded = store.load("fast")
assert isinstance(loaded, LLM)
print(
    "Loaded profile. "
    f"usage:{loaded.usage_id}, "
    f"model: {loaded.model}, "
    f"temperature: {loaded.temperature}."
)

# 5. Delete a profile

store.delete("creative")
print(f"After deletion: {store.list()}")

print("EXAMPLE_COST: 0")
