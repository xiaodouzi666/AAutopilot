"""Strict OpenAI-compatible request and response models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "json_object", "json_schema"] = "json_object"
    json_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def schema_matches_type(self) -> ResponseFormat:
        if self.type == "json_schema" and self.json_schema is None:
            raise ValueError("json_schema is required when response_format.type is json_schema")
        if self.type != "json_schema" and self.json_schema is not None:
            raise ValueError("json_schema is only supported with response_format.type=json_schema")
        return self


class ChatCompletionRequest(BaseModel):
    """Supported subset; unknown OpenAI options fail with a useful 422."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model: str = Field(min_length=1)
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)
    max_tokens: int = Field(default=192, ge=1, le=8192)
    stream: bool = False
    seed: int | None = 20260813
    response_format: ResponseFormat | None = None
    stop: str | list[str] | None = None

    @model_validator(mode="after")
    def validate_stop(self) -> ChatCompletionRequest:
        if isinstance(self.stop, list):
            if not self.stop or len(self.stop) > 4 or any(not item for item in self.stop):
                raise ValueError("stop must contain between one and four non-empty strings")
        elif self.stop == "":
            raise ValueError("stop must not be empty")
        return self

    def upstream_dict(self, *, stream: bool | None = None) -> dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        if stream is not None:
            payload["stream"] = stream
        return payload


class Usage(BaseModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class ChatChoice(BaseModel):
    index: int = 0
    message: AssistantMessage
    finish_reason: Literal["stop", "length", "content_filter", "tool_calls"] = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoice]
    usage: Usage
    system_fingerprint: str | None = None


class ModelCard(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str = "a64pilot"


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelCard]


class OpenAIErrorDetail(BaseModel):
    message: str
    type: str = "invalid_request_error"
    param: str | None = None
    code: str | None = None


class OpenAIErrorResponse(BaseModel):
    error: OpenAIErrorDetail


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """Internal responder result; metadata never changes completion content."""

    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class CompletionResponder(Protocol):
    async def complete(self, request: ChatCompletionRequest) -> CompletionResult:
        """Return one completed response."""
