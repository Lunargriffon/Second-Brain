"""Immutable domain models for the Douyin ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import math
from typing import Any, Mapping
from urllib.parse import urlparse

from .eligibility import Eligibility, EligibilityDecision


class Stage(str, Enum):
    DISCOVERED = "discovered"
    ACQUIRED = "acquired"
    AUDIO_READY = "audio_ready"
    TRANSCRIBED = "transcribed"
    PERSISTED = "persisted"
    INDEXED = "indexed"
    CLEANED = "cleaned"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


_NEXT: dict[Stage, set[Stage]] = {
    Stage.DISCOVERED: {Stage.ACQUIRED, Stage.UNAVAILABLE, Stage.FAILED},
    Stage.ACQUIRED: {Stage.AUDIO_READY, Stage.FAILED},
    Stage.AUDIO_READY: {Stage.TRANSCRIBED, Stage.FAILED},
    Stage.TRANSCRIBED: {Stage.PERSISTED, Stage.FAILED},
    Stage.PERSISTED: {Stage.INDEXED, Stage.CLEANED},
    Stage.INDEXED: {Stage.CLEANED},
    Stage.FAILED: {Stage.DISCOVERED, Stage.UNAVAILABLE},
    Stage.CLEANED: set(),
    Stage.UNAVAILABLE: set(),
}


def _validate_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _validate_https_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("url must be an absolute HTTPS URL")


def _validate_timestamp(value: str | None, field_name: str) -> None:
    if value is None:
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp") from exc


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.start) or self.start < 0:
            raise ValueError("start must be a finite non-negative number")
        if not math.isfinite(self.end) or self.end < self.start:
            raise ValueError("end must be finite and not precede start")

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TranscriptSegment:
        return cls(start=float(value["start"]), end=float(value["end"]), text=str(value["text"]))


@dataclass(frozen=True)
class FavoriteItem:
    work_id: str
    url: str
    author_id: str
    author: str
    caption: str
    hashtags: tuple[str, ...]
    published_at: str | None
    observed_at: str
    stage: Stage = Stage.DISCOVERED
    eligibility: Eligibility | None = None
    eligibility_reasons: tuple[str, ...] = ()
    classifier_version: str | None = None
    classification_input_hash: str | None = None
    last_failure_code: str | None = None
    consecutive_failure_count: int = 0

    def __post_init__(self) -> None:
        _validate_id(self.work_id, "work_id")
        _validate_id(self.author_id, "author_id")
        _validate_https_url(self.url)
        _validate_timestamp(self.published_at, "published_at")
        _validate_timestamp(self.observed_at, "observed_at")
        classification_fields = (
            self.eligibility,
            self.classifier_version,
            self.classification_input_hash,
        )
        if self.eligibility is None:
            if any(value is not None for value in classification_fields[1:]) or self.eligibility_reasons:
                raise ValueError("classification fields must be set together")
        elif (
            not self.eligibility_reasons
            or not self.classifier_version
            or not self.classification_input_hash
        ):
            raise ValueError("classification fields must be set together")
        if self.last_failure_code is None:
            if self.consecutive_failure_count != 0:
                raise ValueError("failure evidence must be set together")
        elif not self.last_failure_code.strip() or self.consecutive_failure_count < 1:
            raise ValueError("failure evidence must be set together")

    def transition(self, target: Stage) -> FavoriteItem:
        target = Stage(target)
        if target not in _NEXT[self.stage]:
            raise ValueError(f"illegal stage transition: {self.stage.value} -> {target.value}")
        return replace(self, stage=target)

    def with_eligibility(self, decision: EligibilityDecision) -> FavoriteItem:
        return replace(
            self,
            eligibility=decision.eligibility,
            eligibility_reasons=decision.reasons,
            classifier_version=decision.classifier_version,
            classification_input_hash=decision.input_hash,
        )

    def with_failure(self, code: str) -> FavoriteItem:
        code = code.strip()
        if not code:
            raise ValueError("failure code must not be empty")
        failed = self if self.stage is Stage.FAILED else self.transition(Stage.FAILED)
        count = (
            failed.consecutive_failure_count + 1
            if failed.last_failure_code == code
            else 1
        )
        return replace(
            failed,
            last_failure_code=code,
            consecutive_failure_count=count,
        )

    def without_failure(self) -> FavoriteItem:
        return replace(self, last_failure_code=None, consecutive_failure_count=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "url": self.url,
            "author_id": self.author_id,
            "author": self.author,
            "caption": self.caption,
            "hashtags": list(self.hashtags),
            "published_at": self.published_at,
            "observed_at": self.observed_at,
            "stage": self.stage.value,
            "eligibility": None if self.eligibility is None else self.eligibility.value,
            "eligibility_reasons": list(self.eligibility_reasons),
            "classifier_version": self.classifier_version,
            "classification_input_hash": self.classification_input_hash,
            "last_failure_code": self.last_failure_code,
            "consecutive_failure_count": self.consecutive_failure_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FavoriteItem:
        return cls(
            work_id=str(value["work_id"]),
            url=str(value["url"]),
            author_id=str(value["author_id"]),
            author=str(value["author"]),
            caption=str(value["caption"]),
            hashtags=tuple(str(tag) for tag in value.get("hashtags", ())),
            published_at=value.get("published_at"),
            observed_at=str(value["observed_at"]),
            stage=Stage(value.get("stage", Stage.DISCOVERED.value)),
            eligibility=(
                None
                if value.get("eligibility") is None
                else Eligibility(str(value["eligibility"]))
            ),
            eligibility_reasons=tuple(
                str(reason) for reason in value.get("eligibility_reasons", ())
            ),
            classifier_version=(
                None
                if value.get("classifier_version") is None
                else str(value["classifier_version"])
            ),
            classification_input_hash=(
                None
                if value.get("classification_input_hash") is None
                else str(value["classification_input_hash"])
            ),
            last_failure_code=(
                None
                if value.get("last_failure_code") is None
                else str(value["last_failure_code"])
            ),
            consecutive_failure_count=int(value.get("consecutive_failure_count", 0)),
        )


@dataclass(frozen=True)
class DouyinRawRecord:
    work_id: str
    url: str
    author_id: str
    author: str
    caption: str
    hashtags: tuple[str, ...]
    published_at: str | None
    observed_at: str
    transcript_text: str
    transcript_segments: tuple[TranscriptSegment, ...]
    transcription_engine: str
    transcription_model: str
    language: str | None
    source_duration_seconds: float
    content_fingerprint: str
    status: str

    def __post_init__(self) -> None:
        _validate_id(self.work_id, "work_id")
        _validate_id(self.author_id, "author_id")
        _validate_https_url(self.url)
        _validate_timestamp(self.published_at, "published_at")
        _validate_timestamp(self.observed_at, "observed_at")
        if any(
            current.start < previous.end
            for previous, current in zip(self.transcript_segments, self.transcript_segments[1:])
        ):
            raise ValueError("segment order must be chronological and non-overlapping")
        if not math.isfinite(self.source_duration_seconds) or self.source_duration_seconds < 0:
            raise ValueError("source_duration_seconds must be finite and non-negative")
        _validate_id(self.content_fingerprint, "content_fingerprint")
        _validate_id(self.status, "status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "url": self.url,
            "author_id": self.author_id,
            "author": self.author,
            "caption": self.caption,
            "hashtags": list(self.hashtags),
            "published_at": self.published_at,
            "observed_at": self.observed_at,
            "transcript_text": self.transcript_text,
            "transcript_segments": [segment.to_dict() for segment in self.transcript_segments],
            "transcription_engine": self.transcription_engine,
            "transcription_model": self.transcription_model,
            "language": self.language,
            "source_duration_seconds": self.source_duration_seconds,
            "content_fingerprint": self.content_fingerprint,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DouyinRawRecord:
        return cls(
            work_id=str(value["work_id"]),
            url=str(value["url"]),
            author_id=str(value["author_id"]),
            author=str(value["author"]),
            caption=str(value["caption"]),
            hashtags=tuple(str(tag) for tag in value.get("hashtags", ())),
            published_at=value.get("published_at"),
            observed_at=str(value["observed_at"]),
            transcript_text=str(value["transcript_text"]),
            transcript_segments=tuple(
                TranscriptSegment.from_dict(segment) for segment in value.get("transcript_segments", ())
            ),
            transcription_engine=str(value["transcription_engine"]),
            transcription_model=str(value["transcription_model"]),
            language=None if value.get("language") is None else str(value["language"]),
            source_duration_seconds=float(value["source_duration_seconds"]),
            content_fingerprint=str(value["content_fingerprint"]),
            status=str(value["status"]),
        )
