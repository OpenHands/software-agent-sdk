from __future__ import annotations

import re
from typing import Final

from openhands.sdk.llm.utils.openhands_provider import LiteLLMCallKwargs


OCI_GENAI_PROVIDER: Final[str] = "oci_genai"
OCI_GENAI_PROVIDER_PREFIX: Final[str] = f"{OCI_GENAI_PROVIDER}/"
OCI_GENAI_PROJECT_HEADER: Final[str] = "openai-project"

_OCI_REGION_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,61}[a-z0-9]$")
_OCI_PROJECT_ID_PATTERN = re.compile(r"^ocid1\.generativeaiproject\.[^\s\r\n]+$")
_OCI_MODEL_ID_PATTERN = re.compile(r"^[^\s\r\n]+$")


def is_oci_genai_model(model: str | None) -> bool:
    return bool(model and model.startswith(OCI_GENAI_PROVIDER_PREFIX))


def normalize_oci_genai_config(region: object, project_id: object) -> tuple[str, str]:
    if not isinstance(region, str) or not region.strip():
        raise ValueError("oci_region is required for OCI Generative AI")
    normalized_region = region.strip().lower()
    if not _OCI_REGION_PATTERN.fullmatch(normalized_region):
        raise ValueError("oci_region must be a valid OCI region identifier")

    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("oci_project_id is required for OCI Generative AI")
    normalized_project_id = project_id.strip()
    if not _OCI_PROJECT_ID_PATTERN.fullmatch(normalized_project_id):
        raise ValueError("oci_project_id must be a Generative AI project OCID")

    return normalized_region, normalized_project_id


def oci_genai_model_name(model: str) -> str:
    model_name = model.removeprefix(OCI_GENAI_PROVIDER_PREFIX).strip()
    if not model_name or not _OCI_MODEL_ID_PATTERN.fullmatch(model_name):
        raise ValueError("OCI Generative AI model ID must be specified")
    return model_name


def oci_genai_base_url(region: str) -> str:
    return f"https://inference.generativeai.{region}.oci.oraclecloud.com/openai/v1"


def oci_genai_litellm_call_kwargs(model: str, region: str) -> LiteLLMCallKwargs:
    model_name = oci_genai_model_name(model)
    return {
        "model": f"openai/{model_name}",
        "api_base": oci_genai_base_url(region),
    }
