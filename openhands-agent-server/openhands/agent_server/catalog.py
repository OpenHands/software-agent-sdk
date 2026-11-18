"""Response policy shared by the default public catalog endpoints."""

from pydantic import BaseModel, Field


class PublicCatalogResponse(BaseModel):
    """Common policy status for the public skill and plugin catalogs."""

    disabled: bool = Field(
        default=False,
        description="True when instance policy disables the default public catalog",
    )
