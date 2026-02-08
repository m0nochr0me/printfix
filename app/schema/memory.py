"""
Request and response models for the Memory REST API.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MemoryAddRequest(BaseModel):
    content: str = Field(..., description="Content to store in memory")
    bucket: str | None = Field(default=None, description="Optional bucket name")


class MemorySearchRequest(BaseModel):
    query: str = Field(..., description="Query to search in memory")
    bucket: str | None = Field(default=None, description="Optional bucket name")
    top_k: int = Field(default=5, ge=1, le=100, description="Number of top results to return")


class MemoryAddResponse(BaseModel):
    status: str
    memory_id: UUID


class MemorySearchResult(BaseModel):
    memory_id: UUID
    content: str
    bucket: str

    model_config = ConfigDict(extra="ignore")


class MemorySearchResponse(BaseModel):
    status: str
    results: list[MemorySearchResult]


class MemoryDeleteResponse(BaseModel):
    status: str


class MemoryClearResponse(BaseModel):
    status: str


class BucketListResponse(BaseModel):
    buckets: list[str]
