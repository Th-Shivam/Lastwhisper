from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Recording(Base):
    __tablename__ = "recordings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_audio_path: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    transcription_status: Mapped[str] = mapped_column(String(30), default="not_started", nullable=False)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcription_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    selected_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transcription_progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(30), default="unavailable", nullable=False)
    extracted_memories: Mapped[str | None] = mapped_column(Text, nullable=True)
    segments: Mapped[list["TranscriptSegment"]] = relationship(back_populates="recording", cascade="all, delete-orphan", order_by="TranscriptSegment.position")
    chunks: Mapped[list["SearchChunk"]] = relationship(back_populates="recording", cascade="all, delete-orphan")
    memories: Mapped[list["SavedMemory"]] = relationship(back_populates="recording", cascade="all, delete-orphan")
    search_index: Mapped["SearchIndexState | None"] = relationship(back_populates="recording", cascade="all, delete-orphan", uselist=False)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(nullable=False)
    end_seconds: Mapped[float] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    recording: Mapped[Recording] = relationship(back_populates="segments")


class SearchChunk(Base):
    __tablename__ = "search_chunks"
    __table_args__ = (UniqueConstraint("recording_id", "position", name="uq_search_chunk_recording_position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_segment_id: Mapped[str] = mapped_column(ForeignKey("transcript_segments.id", ondelete="CASCADE"), index=True)
    segment_ids: Mapped[str] = mapped_column(Text, nullable=False)
    start_seconds: Mapped[float] = mapped_column(nullable=False)
    end_seconds: Mapped[float] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_revision: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    recording: Mapped[Recording] = relationship(back_populates="chunks")
    primary_segment: Mapped[TranscriptSegment] = relationship()


class SearchIndexState(Base):
    __tablename__ = "search_index_state"

    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), primary_key=True)
    transcript_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_revision: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_characters: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_overlap_segments: Mapped[int] = mapped_column(Integer, nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(20), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    recording: Mapped[Recording] = relationship(back_populates="search_index")


class SavedMemory(Base):
    __tablename__ = "saved_memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), index=True)
    segment_id: Mapped[str] = mapped_column(ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_start_seconds: Mapped[float] = mapped_column(nullable=False)
    source_end_seconds: Mapped[float] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    recording: Mapped[Recording] = relationship(back_populates="memories")
    segment: Mapped[TranscriptSegment] = relationship()
