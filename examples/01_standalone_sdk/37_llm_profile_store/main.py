"""Example: Using LLMProfileStore to save and reuse LLM configurations.

This example ships with one pre-generated profile JSON file and creates another
profile at runtime. The checked-in profile comes from a normal save, so secrets
are masked instead of exposed and non-secret fields like `base_url` are kept
when present.
"""

import os
import shutil
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, LLMProfileStore


SCRIPT_DIR = Path(__file__).parent
EXAMPLE_PROFILES_DIR = SCRIPT_DIR / "profiles"
DEFAULT_MODEL = "anthropic/claude-sonnet-4-5-20250929"


profile_store_dir = Path(tempfile.mkdtemp()) / "profiles"
shutil.copytree(EXAMPLE_PROFILES_DIR, profile_store_dir)
store = LLMProfileStore(base_dir=profile_store_dir)

print(f"Seeded profiles: {store.list()}")

api_key = os.getenv("LLM_API_KEY")
creative_llm = LLM(
    usage_id="creative",
    model=os.getenv("LLM_MODEL", DEFAULT_MODEL),
    api_key=SecretStr(api_key) if api_key else None,
    base_url=os.getenv("LLM_BASE_URL"),
    temperature=0.9,
)

# The checked-in fast.json was generated with a normal save, so its api_key is
# masked and any configured base_url would be preserved. This runtime profile
# also avoids persisting the real API key because secrets are masked by default.
store.save("creative", creative_llm)
creative_profile_json = (profile_store_dir / "creative.json").read_text()
if api_key is not None:
    assert api_key not in creative_profile_json

print(f"Stored profiles: {store.list()}")

fast_profile = store.load("fast")
creative_profile = store.load("creative")

print(
    "Loaded fast profile. "
    f"usage: {fast_profile.usage_id}, "
    f"model: {fast_profile.model}, "
    f"temperature: {fast_profile.temperature}."
)
print(
    "Loaded creative profile. "
    f"usage: {creative_profile.usage_id}, "
    f"model: {creative_profile.model}, "
    f"temperature: {creative_profile.temperature}."
)

store.delete("creative")
print(f"After deletion: {store.list()}")

print("EXAMPLE_COST: 0")
