from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Base for response schemas that read directly from ORM objects."""
    model_config = ConfigDict(from_attributes=True)


class MessageResponse(BaseModel):
    detail: str
