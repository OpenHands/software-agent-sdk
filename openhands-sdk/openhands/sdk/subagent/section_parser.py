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

# Single-word tokens. Checked via intersection against the underscore-split
# slug, so only standalone words belong here — a multi-word phrase stored as
# an underscored string (e.g. "pull_request") would never survive the split
# and could never match. Multi-word phrases go in _FOUNDATIONAL_PHRASES.
_FOUNDATIONAL_KEYWORDS: frozenset[str] = frozenset({
    "test", "tests", "testing",
    "build", "building", "commit", "commits", "contributing", "checklist",
})

# Multi-word phrases with no single-word fallback in _FOUNDATIONAL_KEYWORDS.
# Checked via substring match against the underscore-joined slug.
_FOUNDATIONAL_PHRASES: frozenset[str] = frozenset({
    "pull_request",
})

ALWAYS_ACTIVE_SENTINEL = "__always_active__"
_MIN_TOKEN_LEN = 3
_TOP_BODY_NOUNS = 3


def _is_foundational(header_line: str) -> bool:
    """Return True if this section should always be active regardless of triggers."""
    slug = re.sub(r"^#+\s*", "", header_line).strip().lower()
    slug_normalized = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    tokens = set(slug_normalized.split("_"))
    if tokens & _FOUNDATIONAL_KEYWORDS:
        return True
    return any(phrase in slug_normalized for phrase in _FOUNDATIONAL_PHRASES)


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


def _merge_section_agent(
    existing: AgentDefinition,
    new: AgentDefinition,
    header_line: str,
    source_path: str,
) -> AgentDefinition:
    """Merge a colliding section into an already-collected AgentDefinition.

    Two sections that normalize to the same slug (e.g. "## API Conventions"
    and "## API-Conventions") are combined instead of the second being
    silently dropped: body paragraphs from `new` are appended only if not
    already present verbatim in `existing`'s body (so exact duplicates
    don't get re-added), and triggers are unioned.

    Comparison is done on body text only (the part after the header line),
    since the header lines always differ when there's a slug collision —
    comparing full system_prompt strings would never detect a duplicate.
    """
    _, _, existing_body = existing.system_prompt.partition("\n")
    _, _, new_body = new.system_prompt.partition("\n")

    existing_paragraphs = {p.strip() for p in existing_body.split("\n\n") if p.strip()}
    new_paragraphs = [p for p in new_body.split("\n\n") if p.strip()]
    to_append = [p for p in new_paragraphs if p.strip() not in existing_paragraphs]

    if to_append:
        logger.warning(
            f"[SCA] Section '{header_line}' collides with an existing section "
            f"under slug '{existing.name}' (source: {source_path}) — merging "
            f"{len(to_append)} new paragraph(s) instead of dropping."
        )
        merged_prompt = (
            existing.system_prompt
            + "\n\n"
            + header_line
            + "\n"
            + "\n\n".join(to_append)
        )
    else:
        logger.warning(
            f"[SCA] Section '{header_line}' collides with an existing section "
            f"under slug '{existing.name}' (source: {source_path}) — content is "
            f"an exact duplicate, skipping."
        )
        merged_prompt = existing.system_prompt

    merged_triggers = list(dict.fromkeys(existing.triggers + new.triggers))

    return existing.model_copy(
        update={"system_prompt": merged_prompt, "triggers": merged_triggers}
    )


_XML_TAG_PATTERN = re.compile(r"<([A-Z][A-Z0-9_]*)>(.*?)</\1>", re.DOTALL)


def _extract_xml_sections(content: str) -> tuple[list[tuple[str, str]], str]:
    """Extract ``<TAG>...</TAG>`` blocks from content.

    Matches blocks of the form ``<TAG>...</TAG>`` where TAG is all-uppercase
    alphanumeric with underscores (e.g. ``<TESTING>``, ``<DEV_SETUP>``).

    Returns a tuple of (section tuples, remaining content with matched spans
    removed). Removed spans are replaced with a single newline rather than
    an empty string, so line boundaries are preserved — a ``##`` header that
    immediately followed a removed block still matches at the start of a
    line in the remaining content.
    """
    matches = list(_XML_TAG_PATTERN.finditer(content))
    if not matches:
        return [], content

    sections: list[tuple[str, str]] = []
    for match in matches:
        tag = match.group(1)
        body = match.group(2).strip()
        if not body:
            continue
        # Use the tag name as a pseudo-header for trigger derivation
        header_line = f"## {tag.replace('_', ' ').title()}"
        sections.append((header_line, body))

    remaining = content
    for match in reversed(matches):
        remaining = remaining[: match.start()] + "\n" + remaining[match.end() :]

    return sections, remaining


def _build_agent_definitions(
    sections: list[tuple[str, str]],
    source_path: str,
) -> list[AgentDefinition]:
    """Turn (header_line, body) tuples into deduped/merged AgentDefinitions.

    Shared by parse_xml_sections and parse_sections so both formats get
    identical trigger derivation, foundational-section detection, and
    slug-collision handling (merge instead of drop — see
    _merge_section_agent).
    """
    if not sections:
        return []

    tfidf_results = _tfidf_triggers(sections)

    definitions: list[AgentDefinition] = []
    by_name: dict[str, int] = {}
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

        new_def = AgentDefinition(
            name=agent_name,
            description=description,
            system_prompt=f"{header_line}\n{body}",
            triggers=all_triggers,
            source=source_path,
        )

        if agent_name in by_name:
            idx = by_name[agent_name]
            definitions[idx] = _merge_section_agent(
                definitions[idx], new_def, header_line, source_path
            )
        else:
            by_name[agent_name] = len(definitions)
            definitions.append(new_def)

    return definitions


def parse_xml_sections(
    content: str,
    source_path: str = "AGENTS.md",
) -> list[AgentDefinition]:
    """Split file content by uppercase XML-style tags into AgentDefinition instances.

    Matches blocks of the form ``<TAG>...</TAG>`` where TAG is all-uppercase
    alphanumeric with underscores (e.g. ``<TESTING>``, ``<DEV_SETUP>``).

    Returns [] if no such blocks are found.
    """
    sections, _ = _extract_xml_sections(content)
    return _build_agent_definitions(sections, source_path)


def parse_sections(
    content: str,
    source_path: str = "AGENTS.md",
    header_level: str = "##",
) -> list[AgentDefinition]:
    """Split file content into AgentDefinition instances by XML tags and headers.

    Runs both formats together rather than one as a fallback for the other:
    ``<TAG>...</TAG>`` blocks are extracted first (wherever they occur in
    the file, regardless of whether ``##``/``###`` headers exist), then the
    remaining content is split by Markdown ``header_level`` headers. This
    means a file mixing both idioms (e.g. XML tags nested inside or
    alongside Markdown sections) gets every tag recognized as its own
    section instead of being silently absorbed as inert text into
    whichever Markdown section happens to precede it.

    Returns [] if no XML tags and no headers matching header_level are
    found — callers should fall back to loading the file as a single
    AgentDefinition.

    Note: content that appears before the first XML tag or Markdown header
    (a "preamble") is still discarded — see KNOWN_ISSUES.md.
    """
    if header_level not in ("##", "###"):
        raise ValueError(f"header_level must be '##' or '###', got: {header_level!r}")

    xml_sections, remaining_content = _extract_xml_sections(content)

    escaped = re.escape(header_level)
    pattern = re.compile(rf"^({escaped}(?!#)\s+.+)$", re.MULTILINE)
    matches = list(pattern.finditer(remaining_content))

    md_sections: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        header_line = match.group(1).strip()
        body_start = match.end()
        body_end = (
            matches[i + 1].start()
            if i + 1 < len(matches)
            else len(remaining_content)
        )
        body = remaining_content[body_start:body_end].strip()
        md_sections.append((header_line, body))

    return _build_agent_definitions(xml_sections + md_sections, source_path)
