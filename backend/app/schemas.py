from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    original_filename: str
    mime_type: str
    file_size: int
    duration_seconds: int | None
    created_at: datetime
    transcription_status: str
    transcript_text: str | None
    summary: str | None
    error_message: str | None
    transcription_language: str | None
    selected_language: str | None
    transcription_progress: int
    extraction_status: str
    extracted_memories: str | None


class RecordingListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    original_filename: str
    mime_type: str
    file_size: int
    duration_seconds: int | None
    created_at: datetime
    transcription_status: str
    error_message: str | None
    transcription_language: str | None
    selected_language: str | None
    transcription_progress: int
    extraction_status: str


class RecordingUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class TranscriptSegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    position: int
    start_seconds: float
    end_seconds: float
    text: str


class RecordingDetailOut(RecordingOut):
    segments: list[TranscriptSegmentOut]


class TranscriptionStart(BaseModel):
    language: str | None = Field(default=None, min_length=2, max_length=3, pattern=r"^[A-Za-z]{2,3}$")
    replace_existing: bool = False

    @field_validator("language")
    @classmethod
    def normalize_transcription_language(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None


class StatsOut(BaseModel):
    total_recordings: int
    transcribed_recordings: int
    total_duration_seconds: int
    saved_memories: int


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    recording_id: str | None = Field(default=None, min_length=1, max_length=36)
    language: str | None = Field(default=None, max_length=32)
    saved_only: bool = False
    limit: int = Field(default=8, ge=1, le=25)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Search queries must contain at least two non-whitespace characters.")
        return value

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        return value.strip().lower() or None if value is not None else None


class SearchResultOut(BaseModel):
    chunk_id: str
    recording_id: str
    recording_title: str
    recording_date: datetime
    segment_id: str
    segment_ids: list[str]
    start_seconds: float
    end_seconds: float
    text: str
    similarity: float


class SearchResponse(BaseModel):
    results: list[SearchResultOut]
    minimum_similarity: float
    score_explanation: str


class MemoryCreate(BaseModel):
    segment_id: str | None = Field(default=None, min_length=1, max_length=36)
    search_chunk_id: str | None = Field(default=None, min_length=1, max_length=36)
    title: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_source(self):
        if (self.segment_id is None) == (self.search_chunk_id is None):
            raise ValueError("Provide exactly one of segment_id or search_chunk_id.")
        return self

    @field_validator("title", "note")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class MemoryUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_update(self):
        if not self.model_fields_set.intersection({"title", "note"}):
            raise ValueError("Provide a title or note to update.")
        return self

    @field_validator("title", "note")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class MemoryDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    recording_id: str
    segment_id: str
    title: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime
    source_text: str
    source_start_seconds: float
    source_end_seconds: float
    recording_title: str
    recording_date: datetime


class IndexStatusOut(BaseModel):
    available: bool
    model_status: str
    model_loaded: bool
    configured_model: str
    configured_device: str
    total_chunks: int
    indexed_recordings: int
    pending_recordings: int
    failed_recordings: int
    rebuilding: bool
    error: str | None = None


class IndexRebuildErrorOut(BaseModel):
    recording_id: str
    error: str


class IndexRebuildRequest(BaseModel):
    confirm: Literal[True]


class IndexRebuildOut(BaseModel):
    indexed_chunks: int
    indexed_recordings: int
    empty_recordings: int
    failed_recordings: int
    errors: list[IndexRebuildErrorOut]
