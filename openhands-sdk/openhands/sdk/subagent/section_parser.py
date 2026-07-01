"""section_parser.py — Selective Context Activation for subagent definitions.

Splits an AGENTS.md file by Markdown ## headers into a list of AgentDefinition
instances, one per section. Each definition gets keyword triggers derived from
its header tokens (high-signal) and TF-IDF-ranked body tokens (supplementary).
"""

import re
from collections import Counter

from openhands.sdk.logger import get_logger
from openhands.sdk.subagent.schema import AgentDefinition


logger = get_logger(__name__)

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "be", "been", "being", "was",
    "were", "has", "have", "had", "do", "does", "did", "will", "would",
    "should", "can", "could", "may", "might", "not", "no", "this", "that",
    "these", "those", "it", "its", "as", "if", "all", "any", "each",
    "before", "after", "when", "where", "how", "what", "which", "who",
    "than", "then", "so", "only", "also", "use", "run", "add", "make",
    "see", "per", "more", "new", "via", "e.g", "i.e", "instructions",
    "tips", "guide", "overview", "details", "info", "notes", "you",
    "entire", "including", "fixed", "made", "issue", "issues",
}

_SYNONYMS: dict[str, list[str]] = {
    "pr":      ["pull request"],
    "commit":  ["git commit"],
    "test":    ["testing", "tests"],
    "tests":   ["testing", "test"],
    "gradlew": ["gradle"],
    "sdk":     ["temporal sdk"],
    "api":     ["interface"],
}

_SHORT_ALLOWLIST: frozenset[str] = frozenset({"pr", "ci", "cd", "ui", "qa"})

_FOUNDATIONAL_KEYWORDS: frozenset[str] = frozenset({
    "test", "tests", "testing",
    "testing_guidelines", "testing_best_practices", "testing_and_ci",
    "snapshot_test", "snapshot_tests",
    "build_test", "build_and_test", "build_test_and_development",
    "build", "building", "commit", "commits", "contributing",
    "pull_request", "checklist",
})

ALWAYS_ACTIVE_SENTINEL = "__always_active__"
_MIN_TOKEN_LEN = 3
_TOP_BODY_NOUNS = 3


def _is_foundational(header_line: str) -> bool:
    """Return True if this section should always be active regardless of triggers."""
    slug = re.sub(r"^#+\s*", "", header_line).strip().lower()
    slug_normalized = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    tokens = set(slug_normalized.split("_"))
    return bool(tokens & _FOUNDATIONAL_KEYWORDS)


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, filtered by stop words and minimum length."""
    return [
        t
        for t in re.findall(r"[a-z0-9](?:[a-z0-9\-\.]*[a-z0-9])?", text.lower())
        if (len(t) >= _MIN_TOKEN_LEN or t in _SHORT_ALLOWLIST) and t not in _STOP_WORDS
    ]


def _header_triggers(header: str) -> list[str]:
    """Extract trigger keywords from a section header line."""
    clean = re.sub(r"^#+\s*", "", header).strip().rstrip(":")
    tokens = _tokenize(clean)
    triggers = list(tokens)
    phrase = clean.lower().strip()
    if phrase and phrase not in triggers:
        triggers.append(phrase)
    return triggers


def _tfidf_triggers(
    sections: list[tuple[str, str]],
    top_n: int = _TOP_BODY_NOUNS,
) -> list[list[str]]:
    """Return top_n body tokens per section ranked by TF-IDF across all sections."""
    tokenized = [_tokenize(body) for _, body in sections]

    doc_freq: Counter[str] = Counter()
    for tokens in tokenized:
        for tok in set(tokens):
            doc_freq[tok] += 1

    result: list[list[str]] = []
    for tokens in tokenized:
        if not tokens:
            result.append([])
            continue
        tf: Counter[str] = Counter(tokens)
        total = len(tokens)
        scores = {
            tok: (count / total) * (1.0 / doc_freq[tok])
            for tok, count in tf.items()
        }
        top = sorted(scores, key=lambda t: scores[t], reverse=True)[:top_n]
        result.append(top)

    return result


def parse_xml_sections(
    content: str,
    source_path: str = "AGENTS.md",
) -> list[AgentDefinition]:
    """Split file content by uppercase XML-style tags into AgentDefinition instances.

    Matches blocks of the form ``<TAG>...</TAG>`` where TAG is all-uppercase
    alphanumeric with underscores (e.g. ``<TESTING>``, ``<DEV_SETUP>``).
    Used as a fallback when the file has no Markdown ## headers.

    Returns [] if no such blocks are found.
    """
    pattern = re.compile(
        r"<([A-Z][A-Z0-9_]*)>(.*?)</\1>",
        re.DOTALL,
    )
    matches = list(pattern.finditer(content))
    if not matches:
        return []

    sections: list[tuple[str, str]] = []
    for match in matches:
        tag = match.group(1)
        body = match.group(2).strip()
        if not body:
            continue
        # Use the tag name as a pseudo-header for trigger derivation
        header_line = f"## {tag.replace('_', ' ').title()}"
        sections.append((header_line, body))

    if not sections:
        return []

    tfidf_results = _tfidf_triggers(sections)

    definitions: list[AgentDefinition] = []
    for (header_line, body), body_trigger_list in zip(sections, tfidf_results):
        h_triggers = _header_triggers(header_line)

        expanded: list[str] = []
        for t in h_triggers + body_trigger_list:
            expanded.append(t)
            for syn in _SYNONYMS.get(t, []):
                if syn not in expanded:
                    expanded.append(syn)

        all_triggers = list(dict.fromkeys(expanded))

        if _is_foundational(header_line) and ALWAYS_ACTIVE_SENTINEL not in all_triggers:
            all_triggers.append(ALWAYS_ACTIVE_SENTINEL)

        if not all_triggers:
            logger.warning(f"[SCA] No triggers for XML section '{header_line}', skipping.")
            continue

        slug = re.sub(r"^#+\s*", "", header_line).strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
        agent_name = f"sca_{slug}"
        description = re.sub(r"^#+\s*", "", header_line).strip()

        definitions.append(
            AgentDefinition(
                name=agent_name,
                description=description,
                system_prompt=f"{header_line}\n{body}",
                triggers=all_triggers,
                source=source_path,
            )
        )

    return definitions


def parse_sections(
    content: str,
    source_path: str = "AGENTS.md",
    header_level: str = "##",
) -> list[AgentDefinition]:
    """Split file content by Markdown headers into AgentDefinition instances.

    Each section becomes one AgentDefinition carrying only that section's
    content as system_prompt and TF-IDF-derived keyword triggers.

    Returns [] if no headers matching header_level are found — callers
    should fall back to loading the file as a single AgentDefinition.
    """
    if header_level not in ("##", "###"):
        raise ValueError(f"header_level must be '##' or '###', got: {header_level!r}")

    escaped = re.escape(header_level)
    pattern = re.compile(rf"^({escaped}(?!#)\s+.+)$", re.MULTILINE)
    matches = list(pattern.finditer(content))
    if not matches:
        return []

    sections: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        header_line = match.group(1).strip()
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[body_start:body_end].strip()
        sections.append((header_line, body))

    tfidf_results = _tfidf_triggers(sections)

    definitions: list[AgentDefinition] = []
    for (header_line, body), body_trigger_list in zip(sections, tfidf_results):
        h_triggers = _header_triggers(header_line)

        expanded: list[str] = []
        for t in h_triggers + body_trigger_list:
            expanded.append(t)
            for syn in _SYNONYMS.get(t, []):
                if syn not in expanded:
                    expanded.append(syn)

        all_triggers = list(dict.fromkeys(expanded))

        if _is_foundational(header_line) and ALWAYS_ACTIVE_SENTINEL not in all_triggers:
            all_triggers.append(ALWAYS_ACTIVE_SENTINEL)

        if not all_triggers:
            logger.warning(f"[SCA] No triggers for section '{header_line}', skipping.")
            continue

        slug = re.sub(r"^#+\s*", "", header_line).strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
        agent_name = f"sca_{slug}"
        description = re.sub(r"^#+\s*", "", header_line).strip()

        definitions.append(
            AgentDefinition(
                name=agent_name,
                description=description,
                system_prompt=f"{header_line}\n{body}",
                triggers=all_triggers,
                source=source_path,
            )
        )

    return definitions
