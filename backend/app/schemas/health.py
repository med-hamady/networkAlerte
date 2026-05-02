from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str
    app_name: str
    database: str
