"""HTTP endpoints for managing named ``AgentProfile`` launch specs.

Mirrors ``profiles_router.py`` (the LLM ``/api/profiles`` router) but serves the
reference-bearing :class:`~openhands.sdk.profiles.AgentProfile` union and keeps a
*separate* active pointer (``active_agent_profile_id``). Activation here is
pointer-only — unlike the LLM ``/activate`` it must **not** write
``agent_settings`` (the creation-time-only contract).

``POST /{name}/materialize`` performs a dry-run resolve of a profile's LLM and
MCP references and returns :class:`~openhands.sdk.profiles.AgentProfileDiagnostics`
(never raises on dangling refs — those appear in the body).
"""

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import BaseModel, Field, ValidationError

from openhands.agent_server._secrets_exposure import (
    build_expose_context,
    get_cipher,
    get_config,
    parse_expose_secrets_header,
    translate_missing_cipher,
)
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
    get_settings_store,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.profiles import (
    ACPAgentProfile,
    AgentProfileDiagnostics,
    AgentProfileStore,
    OpenHandsAgentProfile,
    ProfileLimitExceeded,
    build_seed_profile,
    resolve_agent_profile_dry_run,
    safe_validation_error_detail,
    save_profile_preserving_identity,
    validate_agent_profile,
)
from openhands.sdk.profiles.agent_profile_store import PROFILE_NAME_PATTERN
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.pydantic_secrets import decrypt_str_with_cipher_or_keep


logger = get_logger(__name__)

agent_profiles_router = APIRouter(prefix="/agent-profiles", tags=["Agent Profiles"])

MAX_AGENT_PROFILES = 50

ProfileName = Annotated[
    str,
    Path(min_length=1, max_length=64, pattern=PROFILE_NAME_PATTERN),
]
ProfileId = Annotated[str, Path(min_length=1, max_length=128)]


class AgentProfileInfo(BaseModel):
    """Summary projection of a stored profile (no secret instantiation)."""

    id: str | None = None
    name: str
    agent_kind: str = "openhands"
    revision: int | None = None
    llm_profile_ref: str | None = None
    mcp_server_refs: list[str] | None = None


class AgentProfileListResponse(BaseModel):
    profiles: list[AgentProfileInfo]
    active_agent_profile_id: str | None = None


class AgentProfileDetailResponse(BaseModel):
    name: str
    profile: dict[str, Any]


class AgentProfileMutationResponse(BaseModel):
    name: str
    message: str


class ActivateAgentProfileResponse(BaseModel):
    id: str
    message: str
    # Always False: activation is pointer-only by contract. The field documents
    # that agent_settings was untouched; materialize (#3717) is the path that
    # resolves a profile into settings.
    agent_settings_applied: bool = False


class RenameAgentProfileRequest(BaseModel):
    new_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=PROFILE_NAME_PATTERN,
    )


@contextmanager
def _store_errors() -> Iterator[None]:
    """Map ``AgentProfileStore`` errors to HTTP responses.

    Mirrors ``profiles_router._store_errors``: ``TimeoutError`` and
    ``ValueError`` only. ``FileNotFoundError`` / ``FileExistsError`` are handled
    inline per-endpoint so each gets a clean, resource-specific message.
    """
    try:
        yield
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent profile store is busy. Please retry.",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


def _decrypt_mcp_tools(tools: dict[str, Any], cipher: Cipher) -> dict[str, Any]:
    """Return a copy of an ``mcp_tools`` dict with env/headers Fernet tokens
    decrypted. Non-Fernet (plaintext) values pass through unchanged."""
    servers = tools.get("mcpServers")
    if not isinstance(servers, dict):
        return tools
    out = copy.deepcopy(tools)
    for server in out["mcpServers"].values():
        if not isinstance(server, dict):
            continue
        for key in ("env", "headers"):
            mapping = server.get(key)
            if isinstance(mapping, dict):
                server[key] = {
                    k: decrypt_str_with_cipher_or_keep(
                        cipher, v, description="MCP env/headers"
                    )
                    for k, v in mapping.items()
                }
    return out


def _decrypt_profile_mcp_tools(
    profile: OpenHandsAgentProfile | ACPAgentProfile, cipher: Cipher | None
) -> OpenHandsAgentProfile | ACPAgentProfile:
    """Decrypt Fernet ``skills[].mcp_tools`` env/headers (no-op without a cipher).

    The store masks/encrypts these on save but has no symmetric load-time
    validator, so values arrive as ciphertext on both GET (at-rest) and save
    (client round-trip). Decrypting here lets GET re-mask from plaintext and
    stops save from double-encrypting an already-encrypted value.
    """
    if cipher is None:
        return profile
    skills = getattr(profile, "skills", None)
    if not skills:
        return profile
    new_skills = [
        skill.model_copy(
            update={"mcp_tools": _decrypt_mcp_tools(skill.mcp_tools, cipher)}
        )
        if skill.mcp_tools
        else skill
        for skill in skills
    ]
    return profile.model_copy(update={"skills": new_skills})


def _seed_default_profile(
    store: AgentProfileStore,
    request: Request,
    settings: PersistedSettings,
    cipher: Cipher | None,
) -> None:
    """Persist one default profile and point ``active_agent_profile_id`` at it.

    The lock spans empty-check + save + pointer write so concurrent first
    requests seed exactly once and the pointer matches the persisted id.
    """
    with _store_errors(), store.lock():
        # Double-checked under the lock: a concurrent first request may have
        # already seeded (the outer emptiness check in the list endpoint is
        # unlocked).
        if store.list():
            return
        profile = build_seed_profile(settings.agent_settings, settings.active_profile)
        # Settings persist skills[].mcp_tools encrypted (and never decrypt on
        # load), so decrypt before re-encrypting at save to avoid double-encrypt.
        profile = _decrypt_profile_mcp_tools(profile, cipher)
        store.save(profile, cipher=cipher, max_profiles=MAX_AGENT_PROFILES)

        profile_id = str(profile.id)
        settings_store = get_settings_store(get_config(request))

        def set_pointer(s: PersistedSettings) -> PersistedSettings:
            s.active_agent_profile_id = profile_id
            return s

        settings_store.update(set_pointer)
        logger.info(f"Seeded default agent profile '{profile.name}' (id={profile_id})")


def _summary_id_for_name(store: AgentProfileStore, name: str) -> str | None:
    """Return the stable id of the profile stored under ``name``, if present."""
    with _store_errors():
        for summary in store.list_summaries():
            if summary.get("name") == name:
                sid = summary.get("id")
                return str(sid) if sid is not None else None
    return None


@agent_profiles_router.get("", response_model=AgentProfileListResponse)
async def list_agent_profiles(request: Request) -> AgentProfileListResponse:
    """List all stored agent profiles and the active pointer.

    On the first call against an empty store with no active pointer, lazily
    seeds one default profile from the current ``agent_settings`` and activates
    it (the one-time migration that replaces a dedicated seed step).
    """
    config = get_config(request)
    settings_store = get_settings_store(config)
    settings = settings_store.load() or PersistedSettings()

    store = get_agent_profile_store()
    with _store_errors():
        existing = store.list()

    if not existing and settings.active_agent_profile_id is None:
        _seed_default_profile(store, request, settings, get_cipher(request))
        settings = settings_store.load() or settings

    with _store_errors():
        summaries = store.list_summaries()

    return AgentProfileListResponse(
        profiles=[AgentProfileInfo(**s) for s in summaries],
        active_agent_profile_id=settings.active_agent_profile_id,
    )


@agent_profiles_router.get("/{name}", response_model=AgentProfileDetailResponse)
async def get_agent_profile(
    request: Request, name: ProfileName
) -> AgentProfileDetailResponse:
    """Get a stored profile.

    Use the ``X-Expose-Secrets`` header to control ``skills[].mcp_tools`` secret
    exposure (``encrypted`` / ``plaintext``); absent, those values are masked.
    """
    expose_mode = parse_expose_secrets_header(request)
    cipher = get_cipher(request)

    store = get_agent_profile_store()
    try:
        with _store_errors():
            profile = store.load(name, cipher=cipher)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile '{name}' not found",
        )

    # The store leaves skills[].mcp_tools encrypted on load; decrypt to plaintext
    # so the expose serializer can correctly redact / re-encrypt / reveal them.
    profile = _decrypt_profile_mcp_tools(profile, cipher)

    context = build_expose_context(expose_mode, cipher)
    with translate_missing_cipher():
        payload = profile.model_dump(mode="json", context=context)

    return AgentProfileDetailResponse(name=name, profile=payload)


@agent_profiles_router.post(
    "/{name}",
    response_model=AgentProfileMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_agent_profile(
    request: Request, name: ProfileName, body: dict[str, Any]
) -> AgentProfileMutationResponse:
    """Save an ``AgentProfile`` under ``name`` (overwriting a namesake).

    The path ``name`` is authoritative — it overrides any ``name`` in the body.
    With ``OH_SECRET_KEY`` configured, ``skills[].mcp_tools`` secrets are
    encrypted at rest; otherwise they are redacted. Returns 409 if creating a
    new profile would exceed ``MAX_AGENT_PROFILES``.
    """
    cipher = get_cipher(request)
    try:
        profile = validate_agent_profile({**body, "name": name})
    except ValidationError as e:
        # Match FastAPI's request-validation shape (``detail`` is a list of error
        # objects), but surface only ``loc``/``type`` — a nested mcp_tools
        # MCPConfig error embeds the input (which may carry secrets) in ``msg``.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=safe_validation_error_detail(e),
        )
    except Exception:
        # Any other validation failure (e.g. SkillValidationError from a
        # malformed mcp_tools, or a schema/migration error) is a client error,
        # never a 500. Stay generic — these messages can embed the input.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid agent profile",
        )

    # A client editing a profile fetched with X-Expose-Secrets: encrypted posts
    # back Fernet tokens; decrypt them so the save re-encrypts the original
    # secret once rather than double-encrypting the token.
    profile = _decrypt_profile_mcp_tools(profile, cipher)

    store = get_agent_profile_store()
    # The id is server-managed (the active pointer is keyed on it): overwrite
    # keeps the namesake's id and bumps revision; create mints a fresh id,
    # ignoring any client-supplied one. ``save_profile_preserving_identity``
    # holds the store lock across read + mint + save so two concurrent creates
    # of the same new name can't both mint an id and clobber each other.
    try:
        with _store_errors():
            save_profile_preserving_identity(
                store, profile, cipher=cipher, max_profiles=MAX_AGENT_PROFILES
            )
    except ProfileLimitExceeded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Agent profile limit reached ({MAX_AGENT_PROFILES}). "
                "Delete a profile before saving a new one."
            ),
        )

    logger.info(f"Saved agent profile '{name}'")
    return AgentProfileMutationResponse(
        name=name, message=f"Agent profile '{name}' saved"
    )


@agent_profiles_router.delete("/{name}", response_model=AgentProfileMutationResponse)
async def delete_agent_profile(
    request: Request, name: ProfileName
) -> AgentProfileMutationResponse:
    """Delete a stored profile (idempotent).

    If the deleted profile was the active one, ``active_agent_profile_id`` is
    cleared.
    """
    store = get_agent_profile_store()
    deleted_id = _summary_id_for_name(store, name)

    with _store_errors():
        store.delete(name)

    if deleted_id is not None:
        config = get_config(request)
        settings_store = get_settings_store(config)
        settings = settings_store.load() or PersistedSettings()
        if settings.active_agent_profile_id == deleted_id:

            def clear_pointer(s: PersistedSettings) -> PersistedSettings:
                s.active_agent_profile_id = None
                return s

            settings_store.update(clear_pointer)
            logger.info(f"Cleared active pointer for deleted profile '{name}'")

    logger.info(f"Deleted agent profile '{name}'")
    return AgentProfileMutationResponse(
        name=name, message=f"Agent profile '{name}' deleted"
    )


@agent_profiles_router.post(
    "/{name}/rename", response_model=AgentProfileMutationResponse
)
async def rename_agent_profile(
    name: ProfileName, body: RenameAgentProfileRequest
) -> AgentProfileMutationResponse:
    """Rename a stored profile atomically.

    The stable ``id`` is preserved, so an active pointer (keyed on ``id``)
    survives the rename untouched. Returns 404 if the source is missing, 409 if
    ``new_name`` is taken.
    """
    store = get_agent_profile_store()
    try:
        with _store_errors():
            store.rename(name, body.new_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile '{name}' not found",
        )
    except FileExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent profile '{body.new_name}' already exists",
        )

    if name == body.new_name:
        message = f"Agent profile '{name}' unchanged (same name)"
    else:
        message = f"Agent profile '{name}' renamed to '{body.new_name}'"
    logger.info(message)
    return AgentProfileMutationResponse(name=body.new_name, message=message)


@agent_profiles_router.post(
    "/{profile_id}/activate", response_model=ActivateAgentProfileResponse
)
async def activate_agent_profile(
    request: Request, profile_id: ProfileId
) -> ActivateAgentProfileResponse:
    """Activate a profile by its stable ``id`` — pointer only.

    Sets ``active_agent_profile_id`` and nothing else: unlike the LLM
    ``/activate``, this does **not** write ``agent_settings`` (the
    creation-time-only contract). Returns 404 if no stored profile has that id.
    """
    store = get_agent_profile_store()
    with _store_errors():
        known_ids = {
            str(s["id"]) for s in store.list_summaries() if s.get("id") is not None
        }
    if profile_id not in known_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile with id '{profile_id}' not found",
        )

    config = get_config(request)
    settings_store = get_settings_store(config)

    def set_pointer(settings: PersistedSettings) -> PersistedSettings:
        settings.active_agent_profile_id = profile_id
        return settings

    try:
        settings_store.update(set_pointer)
    except (OSError, PermissionError):
        logger.error("Failed to activate agent profile - file I/O error")
        raise HTTPException(status_code=500, detail="Failed to activate agent profile")
    except RuntimeError as e:
        # A corrupted / mis-keyed settings file is a server-side integrity
        # failure, not a client conflict.
        logger.error(f"Failed to activate agent profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to activate agent profile",
        )

    logger.info(f"Activated agent profile id '{profile_id}'")
    return ActivateAgentProfileResponse(
        id=profile_id,
        message=f"Agent profile '{profile_id}' activated",
    )


@agent_profiles_router.post(
    "/{name}/materialize",
    response_model=AgentProfileDiagnostics,
)
async def materialize_agent_profile(
    request: Request, name: ProfileName
) -> AgentProfileDiagnostics:
    """Dry-run resolve a profile's LLM/MCP references; return a diagnostics report.

    Dangling LLM/MCP references are reported in the body (valid=False) rather
    than raising — the only error status is 404 (unknown profile name).
    resolved_settings is redacted (api_key_set booleans; no raw secrets).
    """
    cipher = get_cipher(request)

    store = get_agent_profile_store()
    try:
        with _store_errors():
            profile = store.load(name, cipher=cipher)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile '{name}' not found",
        )

    # The store leaves skills[].mcp_tools encrypted on load; decrypt so the
    # resolver builds settings from plaintext (not ciphertext) values.
    profile = _decrypt_profile_mcp_tools(profile, cipher)

    config = get_config(request)
    settings = get_settings_store(config).load() or PersistedSettings()
    mcp_config = settings.agent_settings.mcp_config

    llm_store = get_llm_profile_store()
    return resolve_agent_profile_dry_run(
        profile,
        llm_store=llm_store,
        mcp_config=mcp_config,
        cipher=cipher,
    )
