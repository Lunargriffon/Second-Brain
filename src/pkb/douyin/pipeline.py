"""Durable orchestration for local Douyin favorite transcription."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .eligibility import (
    Eligibility,
    KnowledgeValueClassifier,
    VideoMetadata,
)
from .manifest import ManifestStore
from .media import AcquisitionDisposition, AcquisitionFailure, MediaPaths
from .models import DouyinRawRecord, FavoriteItem, Stage
from .transcription import TranscriptResult


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    voiced_seconds: float


@dataclass
class RunAudit:
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    stopped: bool = False
    cleanup_pending: int = 0
    reason_counts: dict[str, int] = field(default_factory=dict)


class MediaManager(Protocol):
    def prepare(self, work_id: str) -> MediaPaths: ...
    def extract_audio(self, paths: MediaPaths) -> None: ...
    def cleanup(self, work_dir: Path) -> None: ...


class Acquirer(Protocol):
    def acquire(self, favorite: FavoriteItem, destination: Path) -> None: ...


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, voiced_seconds: float) -> TranscriptResult: ...


class DurableJsonlStore:
    """Append records once, forcing each accepted line to stable storage."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def contains(self, work_id: str) -> bool:
        if not self.path.exists():
            return False
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip() and json.loads(line).get("work_id") == work_id:
                    return True
        return False

    def append_once(self, record: Mapping[str, Any] | DouyinRawRecord) -> bool:
        payload = record.to_dict() if isinstance(record, DouyinRawRecord) else dict(record)
        work_id = str(payload["work_id"])
        if self.contains(work_id):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return True


class DouyinPipeline:
    def __init__(
        self,
        *,
        manifest: ManifestStore,
        raw_store: DurableJsonlStore,
        media: MediaManager,
        acquirer: Acquirer,
        transcriber: Transcriber,
        probe: Callable[[MediaPaths], MediaInfo],
        classifier: KnowledgeValueClassifier | None = None,
        force_reclassify: bool = False,
    ) -> None:
        self.manifest = manifest
        self.raw_store = raw_store
        self.media = media
        self.acquirer = acquirer
        self.transcriber = transcriber
        self.probe = probe
        self.classifier = classifier
        self.force_reclassify = force_reclassify

    def run(self) -> RunAudit:
        counts: Counter[str] = Counter()
        errors: Counter[str] = Counter()
        reason_counts: Counter[str] = Counter()
        if self.classifier is not None:
            try:
                for entry in self.manifest.items():
                    decision = self.classifier.classify(
                        VideoMetadata(
                            caption=entry.caption,
                            hashtags=entry.hashtags,
                            author=entry.author,
                        )
                    )
                    if self.force_reclassify or self.manifest.needs_classification(
                        entry.work_id,
                        decision.classifier_version,
                        decision.input_hash,
                    ):
                        self.manifest.set_eligibility(entry.work_id, decision)
                classified = self.manifest.items()
            except Exception:
                return RunAudit(
                    counts={},
                    errors={"classification_failed": 1},
                    stopped=True,
                    cleanup_pending=0,
                )
            counts["classified"] = len(classified)
            counts["eligible"] = sum(
                entry.eligibility is Eligibility.KEEP for entry in classified
            )
            counts["excluded"] = sum(
                entry.eligibility is Eligibility.EXCLUDE for entry in classified
            )
            for entry in classified:
                if entry.eligibility is Eligibility.EXCLUDE:
                    reason_counts.update(entry.eligibility_reasons)

        pending_items = self.manifest.pending()
        counts["selected"] = len(pending_items)
        stopped = False

        for pending in pending_items:
            if stopped:
                break
            item = self.manifest.get(pending.work_id)
            if item.stage is Stage.FAILED:
                item = self.manifest.update(item.work_id, Stage.DISCOVERED)
            paths = self.media.prepare(item.work_id)

            try:
                if item.stage is Stage.DISCOVERED:
                    self.acquirer.acquire(item, paths.video)
                    item = self.manifest.update(item.work_id, Stage.ACQUIRED)
                if item.stage is Stage.ACQUIRED:
                    self.media.extract_audio(paths)
                    item = self.manifest.update(item.work_id, Stage.AUDIO_READY)
                if item.stage in {Stage.AUDIO_READY, Stage.TRANSCRIBED}:
                    info = self.probe(paths)
                    result = self.transcriber.transcribe(paths.audio, info.voiced_seconds)
                    if item.stage is Stage.AUDIO_READY:
                        item = self.manifest.update(item.work_id, Stage.TRANSCRIBED)
                    record = self._record(item, result, info)
                    appended = self.raw_store.append_once(record)
                    if appended:
                        counts["persisted"] += 1
                    item = self.manifest.update(item.work_id, Stage.PERSISTED)
            except AcquisitionFailure as exc:
                errors[exc.code] += 1
                if exc.disposition is AcquisitionDisposition.UNAVAILABLE:
                    self.manifest.update(item.work_id, Stage.UNAVAILABLE)
                    counts["unavailable"] += 1
                else:
                    self._mark_failed(item)
                    counts["failed"] += 1
                    stopped = exc.disposition is AcquisitionDisposition.STOP_RUN
                continue
            except Exception:
                errors[self._phase_error(item.stage)] += 1
                self._mark_failed(item)
                counts["failed"] += 1
                continue

            item = self.manifest.get(item.work_id)
            if item.stage in {Stage.PERSISTED, Stage.INDEXED}:
                try:
                    self.media.cleanup(paths.work_dir)
                except Exception:
                    errors["cleanup_failed"] += 1
                    counts["failed"] += 1
                    continue
                self.manifest.update(item.work_id, Stage.CLEANED)
                counts["cleaned"] += 1

        cleanup_pending = sum(
            entry.stage in {Stage.PERSISTED, Stage.INDEXED}
            for entry in self.manifest.items()
        )
        return RunAudit(
            dict(counts),
            dict(errors),
            stopped,
            cleanup_pending,
            dict(reason_counts),
        )

    @staticmethod
    def _phase_error(stage: Stage) -> str:
        return {
            Stage.DISCOVERED: "acquire_failed",
            Stage.ACQUIRED: "audio_extract_failed",
            Stage.AUDIO_READY: "transcription_failed",
            Stage.TRANSCRIBED: "persistence_failed",
        }.get(stage, "processing_failed")

    def _mark_failed(self, item: FavoriteItem) -> None:
        if item.stage in {Stage.DISCOVERED, Stage.ACQUIRED, Stage.AUDIO_READY, Stage.TRANSCRIBED}:
            self.manifest.update(item.work_id, Stage.FAILED)

    @staticmethod
    def _record(
        item: FavoriteItem, result: TranscriptResult, info: MediaInfo
    ) -> DouyinRawRecord:
        fingerprint = hashlib.sha256(result.text.encode("utf-8")).hexdigest()
        return DouyinRawRecord(
            work_id=item.work_id,
            url=item.url,
            author_id=item.author_id,
            author=item.author,
            caption=item.caption,
            hashtags=item.hashtags,
            published_at=item.published_at,
            observed_at=item.observed_at,
            transcript_text=result.text,
            transcript_segments=result.segments,
            transcription_engine=result.engine,
            transcription_model=result.model,
            language=result.language,
            source_duration_seconds=info.duration_seconds,
            content_fingerprint=fingerprint,
            status="transcribed",
        )
