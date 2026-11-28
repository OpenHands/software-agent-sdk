# prompt_utils.py
import os
import re
import sys
from functools import lru_cache
from pickle import UnpicklingError

from jinja2 import (
    BaseLoader,
    Environment,
    FileSystemBytecodeCache,
    Template,
    TemplateNotFound,
    TemplateSyntaxError,
)
from jinja2.bccache import Bucket

from openhands.sdk.logger import get_logger
from openhands.sdk.utils.path import get_user_persistence_dir


logger = get_logger(__name__)


class _BestEffortBytecodeCache(FileSystemBytecodeCache):
    """Stop using a failed disk cache while continuing to compile templates."""

    def __init__(self, directory: str) -> None:
        super().__init__(directory=directory)
        self._disabled = False

    def _disable(self, error: Exception) -> None:
        self._disabled = True
        logger.warning(
            "Disabling Jinja bytecode cache at %s: %s", self.directory, error
        )

    def load_bytecode(self, bucket: Bucket) -> None:
        if self._disabled:
            return
        try:
            super().load_bytecode(bucket)
        except (OSError, EOFError, UnpicklingError) as error:
            bucket.reset()
            self._disable(error)

    def dump_bytecode(self, bucket: Bucket) -> None:
        if self._disabled:
            return
        try:
            super().dump_bytecode(bucket)
        except OSError as error:
            self._disable(error)


class FlexibleFileSystemLoader(BaseLoader):
    """A Jinja2 loader that supports both relative paths (within a base directory)
    and absolute paths anywhere on the filesystem.
    """

    def __init__(self, searchpath: str):
        self.searchpath = os.path.abspath(searchpath)

    def get_source(self, environment, template):  # noqa: ARG002
        # If template is an absolute path, use it directly
        if os.path.isabs(template):
            path = template
        else:
            # Otherwise, look for it in the searchpath
            path = os.path.join(self.searchpath, template)

        if not os.path.exists(path):
            raise TemplateNotFound(template)

        mtime = os.path.getmtime(path)
        with open(path, encoding="utf-8") as f:
            source = f.read()

        def uptodate():
            try:
                return os.path.getmtime(path) == mtime
            except OSError:
                return False

        return source, path, uptodate


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
    cache_folder = get_user_persistence_dir() / "cache" / "jinja"
    bcc = None
    try:
        cache_folder.mkdir(parents=True, exist_ok=True)
        bcc = _BestEffortBytecodeCache(directory=str(cache_folder))
    except OSError as error:
        logger.warning(
            "Cannot create Jinja bytecode cache at %s: %s", cache_folder, error
        )
    env = Environment(
        loader=FlexibleFileSystemLoader(prompt_dir),
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
    except TemplateNotFound as error:
        raise FileNotFoundError(
            f"Prompt file {os.path.join(prompt_dir, template_name)} not found"
        ) from error
    except TemplateSyntaxError as error:
        raise type(error)(
            error.message or str(error),
            error.lineno,
            name=error.name,
            filename=error.filename,
        ) from error


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
        TemplateSyntaxError: If the template cannot be compiled.
        UnicodeDecodeError: If the template is not valid UTF-8.

    Bytecode-cache failures are logged and rendering continues without disk caching.
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
