"""The resolution judge: is anything left for the user after a turn?

Usage: resolution.py verdicts|judge

- verdicts: `verdict_from_judgments` on fixed judgment sets, no model.
- judge: `resolution_judge(fallback, store)` on an empty profile store saves
  the `resolution_judge` profile from the fallback LLM, then
  `judge_resolution` asks it the questions through a local OpenAI-compatible
  endpoint that answers with fixed JSON; a second call with another fallback
  reuses the saved profile; a cipher store keeps the key encrypted on disk; a
  reply that is not JSON raises.

The endpoint is `S16/fake_openai.py`, so no key or network is needed.
"""

import asyncio
import json
import sys

from pydantic import SecretStr
from S16 import fake_openai
from sdk_verify import emit, run_paths

from openhands.sdk import LLM
from openhands.sdk.conversation.resolution import (
    RESOLUTION_JUDGE_PROFILE_NAME,
    RESOLUTION_QUESTIONS,
    ResolutionState,
    judge_resolution,
    resolution_judge,
    verdict_from_judgments,
)
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.utils.cipher import Cipher


MODE = sys.argv[1]
LOW = {
    "asks_user": 0.05,
    "hands_off_review": 0.05,
    "offers_choice": 0.05,
    "asks_to_check": 0.05,
    "reports_problem": 0.05,
}


def verdict(judgments: dict[str, float]) -> dict:
    v = verdict_from_judgments(judgments, model="qa-judge")
    return {
        "owed": v.owed,
        "probability": round(v.probability, 4),
        "resolved": v.resolved,
        "needs_user": v.needs_user,
        "note": v.note(),
        "model": v.model,
    }


if MODE == "verdicts":
    emit(
        {
            "questions": sorted(RESOLUTION_QUESTIONS),
            "done": verdict({"handled": 0.95, "task_done": 0.9, **LOW}),
            "asks": verdict(
                {"handled": 0.95, "task_done": 0.9, **LOW, "asks_user": 0.8}
            ),
            "review": verdict(
                {"handled": 0.95, "task_done": 0.9, **LOW, "hands_off_review": 0.9}
            ),
            "unfinished": verdict({"handled": 0.95, "task_done": 0.4, **LOW}),
            "unclear": verdict(
                {"handled": 0.95, "task_done": 0.9, **LOW, "offers_choice": 0.3}
            ),
            "problem": verdict(
                {"handled": 0.95, "task_done": 0.9, **LOW, "reports_problem": 0.7}
            ),
            "out_of_range": verdict(
                {"handled": 7, "task_done": "x", **LOW}  # type: ignore[dict-item]
            ),
        }
    )
    sys.exit(0)

REPLY = json.dumps(
    {
        "handled": {"probability": 0.97},
        "task_done": {"probability": 0.92},
        "asks_user": {"probability": 0.9},
        "hands_off_review": {"probability": 0.02},
        "offers_choice": {"probability": 0.02},
        "asks_to_check": {"probability": 0.02},
        "reports_problem": {"probability": 0.02},
    }
)
STATE = ResolutionState(
    request="Rename the setting.",
    reply="Renamed it. Should I also update the docs?",
    actions=["edit settings.py"],
    task="Clean up the settings module.",
)


def fallback(base_url: str, model: str, key: str) -> LLM:
    return LLM(
        model=model,
        api_key=SecretStr(key),
        base_url=base_url,
        usage_id="agent",
        num_retries=3,
        timeout=600,
    )


base = run_paths()["run"] / "profiles" / "resolution"
plain_dir, cipher_dir = base / "plain", base / "cipher"
plain_dir.mkdir(parents=True, exist_ok=True)
cipher_dir.mkdir(parents=True, exist_ok=True)
url = fake_openai.serve(REPLY)
store = LLMProfileStore(plain_dir)
before = store.list()

judge = resolution_judge(fallback(url, "openai/qa-judge-a", "qa-key-a"), store)
first = asyncio.run(judge_resolution(judge, STATE))
saved = store.load(RESOLUTION_JUDGE_PROFILE_NAME)
request = fake_openai.requests[-1]
prompt = json.dumps(request.get("messages"))

again = resolution_judge(fallback(url, "openai/qa-judge-b", "qa-key-b"), store)

cipher = Cipher("qa-s04-resolution-secret")
cipher_store = LLMProfileStore(cipher_dir)
resolution_judge(fallback(url, "openai/qa-judge-c", "qa-key-c"), cipher_store, cipher=cipher)
cipher_file = (cipher_dir / f"{RESOLUTION_JUDGE_PROFILE_NAME}.json").read_text()
cipher_loaded = cipher_store.load(RESOLUTION_JUDGE_PROFILE_NAME, cipher=cipher)

bad_dir = base / "bad"
bad_dir.mkdir(parents=True, exist_ok=True)
bad_url = fake_openai.serve("I think it is resolved.")
bad_judge = resolution_judge(
    fallback(bad_url, "openai/qa-judge-bad", "qa-key-bad"), LLMProfileStore(bad_dir)
)
calls_before_bad = len(fake_openai.requests)
try:
    asyncio.run(judge_resolution(bad_judge, STATE))
    bad_error = None
except Exception as exc:  # what a caller sees
    bad_error = f"{type(exc).__name__}: {exc}"

emit(
    {
        "profiles_before": before,
        "profiles_after": store.list(),
        "saved": {
            "model": saved.model,
            "base_url": saved.base_url,
            "api_key_kept": saved.api_key is not None
            and saved.api_key.get_secret_value() == "qa-key-a",
            "usage_id": saved.usage_id,
        },
        "judge": {
            "model": judge.llm.model,
            "usage_id": judge.llm.usage_id,
            "llm_timeout": judge.llm.timeout,
            "llm_num_retries": judge.llm.num_retries,
            "timeout": judge.timeout,
            "num_retries": judge.num_retries,
        },
        "verdict": {
            "owed": first.owed,
            "resolved": first.resolved,
            "needs_user": first.needs_user,
            "probability": round(first.probability, 4),
            "model": first.model,
            "judgments": sorted(first.judgments),
        },
        "request": {
            "model": request.get("model"),
            "has_state": "Rename the setting." in prompt,
            "names_questions": all(name in prompt for name in RESOLUTION_QUESTIONS),
        },
        "reused_model": again.llm.model,
        "cipher": {
            "plain_key_on_disk": "qa-key-c" in cipher_file,
            "loaded_key_matches": cipher_loaded.api_key is not None
            and cipher_loaded.api_key.get_secret_value() == "qa-key-c",
        },
        "bad_reply_error": bad_error,
        "bad_reply_requests": len(fake_openai.requests) - calls_before_bad,
    }
)
