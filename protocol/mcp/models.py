from __future__ import annotations

from pydantic import BaseModel, Field


class MCPToolDescriptor(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0.0"
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
