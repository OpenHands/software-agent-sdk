"""Synthetic memory challenges with reproducible, independent answer values.

These compare natural strategies. A targeted search can reveal an answer without
paging through its full record; ``require_full_read`` marks a separate mechanism
check, not a necessary condition for answer correctness.
"""

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryCase:
    name: str
    windows: tuple[tuple[str, ...], ...]
    question: str
    expected: dict[str, str | int]
    query: str
    delayed_question: bool = False
    require_full_read: bool = False
    target_marker: str = ""


def _token(rng: random.Random, prefix: str) -> str:
    return f"{prefix}-{rng.getrandbits(64):016x}"


def _multi_reset(rng: random.Random) -> MemoryCase:
    title = f"Deployment journal {_token(rng, 'J')}"
    release = _token(rng, "REL")
    old_tag, current_tag = (_token(rng, "TAG") for _ in range(2))
    old_auth, current_auth = (_token(rng, "AUTH") for _ in range(2))
    old_retry, middle_retry, current_retry = rng.sample(range(2, 10), 3)
    return MemoryCase(
        name="multi_reset",
        windows=(
            (
                f"{title}\nThe initial release_token is {release}.",
                f"{title}\nInitial configuration: deployment_tag={old_tag}; "
                f"authorization_code={old_auth}; retry_limit={old_retry}.",
            ),
            (
                f"{title}\nCorrection: replace deployment_tag with {current_tag}. "
                "The previous deployment_tag is revoked; other fields are unchanged.",
                f"{title}\nUpdate retry_limit to {middle_retry}. "
                "All other fields are unchanged.",
            ),
            (
                f"{title}\nCorrection: replace authorization_code with "
                f"{current_auth}. The previous authorization_code is revoked; "
                "other fields are unchanged.",
                f"{title}\nFinal update: retry_limit={current_retry}. "
                "All other fields are unchanged.",
            ),
        ),
        question=(
            f"For {title}, return the initial release_token and the current "
            "deployment_tag, authorization_code and retry_limit as a JSON object."
        ),
        expected={
            "release_token": release,
            "deployment_tag": current_tag,
            "authorization_code": current_auth,
            "retry_limit": current_retry,
        },
        query=title,
    )


def _delayed_question(rng: random.Random) -> MemoryCase:
    title = f"Operations inventory {_token(rng, 'INV')}"
    records = [
        (f"R-{i + 1:03d}", _token(rng, "ART"), _token(rng, "APR")) for i in range(30)
    ]
    target_id, artifact, approval = rng.choice(records)
    messages = tuple(
        title
        + "\n"
        + "\n".join(
            f"Record {record_id} | artifact_token={artifact_token} | "
            f"approval_token={approval_token}"
            for record_id, artifact_token, approval_token in records[start : start + 15]
        )
        for start in (0, 15)
    )
    return MemoryCase(
        name="delayed_question",
        windows=(messages,),
        question=(
            f"For record {target_id} in {title}, return artifact_token and "
            "approval_token as a JSON object with those two keys."
        ),
        expected={"artifact_token": artifact, "approval_token": approval},
        query=title,
        delayed_question=True,
        target_marker=artifact,
    )


def _long_history(rng: random.Random) -> MemoryCase:
    title = f"Archived manifest {_token(rng, 'MAN')}"
    records = [
        {
            "entry_id": f"E-{i + 1:03d}",
            "artifact_token": _token(rng, "ART"),
            "approval_token": _token(rng, "APR"),
            "owner": f"team-{rng.randrange(100):02d}",
            "zone": f"zone-{rng.randrange(100):02d}",
            "batch": _token(rng, "B"),
            "checksum": _token(rng, "CHK"),
        }
        for i in range(80)
    ]
    target = rng.choice(records[60:])
    index = "\n".join(f"Entry {record['entry_id']}" for record in records)
    format_notes = (
        "Manifest format: all entries use the same fields and status label. "
        "The index lists identifiers only; full entry fields follow the separator.\n"
    ) * 4
    details = "\n".join(
        f"Entry {record['entry_id']} | artifact_token={record['artifact_token']} | "
        f"approval_token={record['approval_token']} | owner={record['owner']} | "
        f"zone={record['zone']} | batch={record['batch']} | "
        f"checksum={record['checksum']} | status=verified"
        for record in records
    )
    body = f"{title}\nEntry index\n{index}\n{format_notes}\nEntry details\n{details}"
    return MemoryCase(
        name="long_history",
        windows=((body,),),
        question=(
            f"For entry {target['entry_id']} in {title}, return artifact_token and "
            "approval_token as a JSON object with those two keys."
        ),
        expected={
            "artifact_token": target["artifact_token"],
            "approval_token": target["approval_token"],
        },
        query=title,
        delayed_question=True,
        require_full_read=True,
        target_marker=target["artifact_token"],
    )


def make_challenge_cases(seed: int, repeat: int) -> tuple[MemoryCase, ...]:
    rng = random.Random(f"{seed}:{repeat}")
    return (_multi_reset(rng), _delayed_question(rng), _long_history(rng))
