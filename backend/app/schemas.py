from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


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
    language: str | None = Field(default=None, min_length=2, max_length=16)


class StatsOut(BaseModel):
    total_recordings: int
    transcribed_recordings: int
    total_duration_seconds: int
    saved_memories: int

