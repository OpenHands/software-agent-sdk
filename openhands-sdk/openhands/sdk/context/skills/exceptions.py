class SkillError(Exception):
    """Base exception for all skill errors."""

    pass


class SkillValidationError(SkillError):
    """Raised when there's a validation error in skill metadata."""

    def __init__(
        self,
        message: str = "Skill validation failed",
        errors: list[str] | None = None,
    ) -> None:
        self.errors = errors or []
        if self.errors:
            full_message = f"{message}: {'; '.join(self.errors)}"
        else:
            full_message = message
        super().__init__(full_message)
