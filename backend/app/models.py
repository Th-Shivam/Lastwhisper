from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class Recording(Base):
    __tablename__ = "recordings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_audio_path: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
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


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(nullable=False)
    end_seconds: Mapped[float] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    recording: Mapped[Recording] = relationship(back_populates="segments")

