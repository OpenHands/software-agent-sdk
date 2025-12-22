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
        super().__init__(message)
        self.errors = errors or []

    def __str__(self) -> str:
        if self.errors:
            return f"{self.args[0]}: {'; '.join(self.errors)}"
        return str(self.args[0])
