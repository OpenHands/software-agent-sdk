# prompt_utils.py
import os
import re
import sys
from functools import lru_cache

from jinja2 import Environment, FileSystemBytecodeCache, FileSystemLoader, Template


def refine(text: str) -> str:
    if sys.platform == "win32":
        text = re.sub(r"\bterminal\b", "execute_powershell", text, flags=re.IGNORECASE)
        text = re.sub(
            r"(?<!execute_)(?<!_)\bbash\b", "powershell", text, flags=re.IGNORECASE
        )
    return text


@lru_cache(maxsize=64)
def _get_env(prompt_dir: str) -> Environment:
    if not prompt_dir:
        raise ValueError("prompt_dir is required")
    # BytecodeCache avoids reparsing templates across processes
    # Use user-specific cache directory to avoid permission issues
    # in multi-user environments
    cache_folder = os.path.join(os.path.expanduser("~"), ".openhands", "cache", "jinja")
    os.makedirs(cache_folder, exist_ok=True)
    bcc = FileSystemBytecodeCache(directory=cache_folder)
    env = Environment(
        loader=FileSystemLoader(prompt_dir),
        bytecode_cache=bcc,
        autoescape=False,
    )
    # Optional: expose refine as a filter so templates can use {{ text|refine }}
    env.filters["refine"] = refine
    return env


@lru_cache(maxsize=256)
def _get_template(prompt_dir: str, template_name: str) -> Template:
    env = _get_env(prompt_dir)
    try:
        return env.get_template(template_name)
    except Exception:
        raise FileNotFoundError(
            f"Prompt file {os.path.join(prompt_dir, template_name)} not found"
        )


def render_template(prompt_dir: str, template_name: str, **ctx) -> str:
    """Render a Jinja2 template.

    Args:
        prompt_dir: The base directory for relative template paths.
        template_name: The template filename. Can be either:
            - A relative filename (e.g., "system_prompt.j2") loaded from prompt_dir
            - An absolute path (e.g., "/path/to/custom_prompt.j2")
        **ctx: Template context variables.

    Returns:
        Rendered template string.

    Raises:
        FileNotFoundError: If the template file cannot be found.
    """
    # If template_name is an absolute path, extract directory and filename
    if os.path.isabs(template_name):
        # Check if the file exists before trying to load it
        if not os.path.isfile(template_name):
            raise FileNotFoundError(f"Prompt file {template_name} not found")
        actual_dir = os.path.dirname(template_name)
        actual_filename = os.path.basename(template_name)
        tpl = _get_template(actual_dir, actual_filename)
    else:
        tpl = _get_template(prompt_dir, template_name)
    return refine(tpl.render(**ctx).strip())
