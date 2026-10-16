class ToolError(Exception):
    """Raised when a tool encounters an error."""

    message: str

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)

    def __str__(self):
        return self.message


class EditorToolParameterMissingError(ToolError):
    """Raised when a required parameter is missing for a tool command.

    The base message is preserved as a prefix; when a recovery hint exists
    for the (command, parameter) pair, it is appended so the model can fix
    the call on the next attempt instead of burning turns retrying blindly
    (same motivation as the terminal literal-argument guard, SDK-5).
    """

    command: str
    parameter: str

    # Recovery hints keyed by (command, parameter). Only pairs that benefit
    # from guidance are listed; others fall back to the base message.
    _HINTS: dict[tuple[str, str], str] = {
        ("str_replace", "old_str"): (
            "To replace text, first run `view` on the file and copy `old_str` "
            "EXACTLY from its output, including all whitespace and line "
            "endings — `str_replace` fails if the anchor does not match "
            "byte-for-byte. To insert new content after a known line, use "
            "`insert` (requires `insert_line` and `new_str`); to create a "
            "new file, use `create` with `file_text`."
        ),
        ("str_replace", "new_str"): (
            "`str_replace` needs `new_str` (the replacement text). It may be "
            "an empty string (\"\") to delete the text matched by `old_str`."
        ),
        ("create", "file_text"): (
            "`create` writes a NEW file and needs `file_text` with its full "
            "content. If the file already exists, use `str_replace` "
            "(requires `old_str` copied from a prior `view`) or `insert`."
        ),
        ("insert", "insert_line"): (
            "`insert` places text AFTER the given line number; provide "
            "`insert_line` (0 inserts before the first line) and `new_str`. "
            "Run `view` first to pick the right line number."
        ),
        ("insert", "new_str"): (
            "`insert` needs `new_str` (the text to insert after `insert_line`)."
        ),
    }

    def __init__(self, command: str, parameter: str):
        self.command = command
        self.parameter = parameter
        self.message: str = (
            f"Parameter `{parameter}` is required for command: {command}."
        )
        hint = self._HINTS.get((command, parameter))
        if hint:
            self.message = f"{self.message}\n\nHint: {hint}"


class EditorToolParameterInvalidError(ToolError):
    """Raised when a parameter is invalid for a tool command."""

    parameter: str
    value: str

    def __init__(self, parameter: str, value: str, hint: str | None = None):
        self.parameter = parameter
        self.value = value
        self.message: str = (
            f"Invalid `{parameter}` parameter: {value}. {hint}"
            if hint
            else f"Invalid `{parameter}` parameter: {value}."
        )


class FileValidationError(ToolError):
    """Raised when a file fails validation checks (size, type, etc.)."""

    path: str
    reason: str

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        self.message: str = f"File validation failed for {path}: {reason}"
        super().__init__(self.message)
