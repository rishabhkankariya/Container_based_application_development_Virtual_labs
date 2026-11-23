"""
Asynchronous local-outbox to cloud-PostgreSQL synchronization worker.

SQLite remains the operational source of truth in local-first mode. The worker
only acknowledges an event locally after the PostgreSQL transaction commits.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Any, Dict, List, Optional

from backend.db import (
    get_cloud_db,
    get_pending_sync_events,
    mark_sync_event_synced,
    record_sync_event_failure,
)

BASE_INTERVAL_SECONDS = max(5.0, float(os.environ.get("VLAB_SYNC_INTERVAL_SECONDS", "12")))
MAX_BACKOFF_SECONDS = max(BASE_INTERVAL_SECONDS, float(os.environ.get("VLAB_SYNC_MAX_BACKOFF_SECONDS", "300")))
BATCH_SIZE = max(1, int(os.environ.get("VLAB_SYNC_BATCH_SIZE", "50")))


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    """Read a DB row by name or position, accommodating driver row variants."""
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[index]


def _normalise_steps(value: Any) -> List[int]:
    """Validate and return unique, sorted positive integer step IDs."""
    if isinstance(value, str):
        value = json.loads(value or "[]")
    if not isinstance(value, list):
        raise ValueError("steps_completed must be a JSON list")
    steps: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            raise ValueError("Boolean values are not valid step IDs")
        try:
            step = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid step ID: {item!r}") from exc
        if step < 1:
            raise ValueError(f"Step IDs must be positive: {step}")
        steps.add(step)
    return sorted(steps)


def ensure_cloud_schema(cloud) -> None:
    """Create only the tables needed by this worker; safe to call repeatedly."""
    cloud.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_sync_events (
            operation_id VARCHAR(36) PRIMARY KEY,
            processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cloud.execute(
        """
        CREATE TABLE IF NOT EXISTS lab_progress (
            id BIGSERIAL PRIMARY KEY,
            student_id VARCHAR(50) NOT NULL,
            lab_number INTEGER NOT NULL,
            steps_completed TEXT NOT NULL DEFAULT '[]',
            verification_passed INTEGER NOT NULL DEFAULT 0,
            completed_at TIMESTAMP NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (student_id, lab_number)
        )
        """
    )
    cloud.commit()


def apply_lab_progress_event(cloud, event: Dict[str, Any]) -> None:
    """
    Apply one event and its idempotency marker in a single PostgreSQL transaction.

    Progress steps merge by set union. Verification remains a separate monotonic
    flag and is never inferred from the number of completed steps.
    """
    operation_id = str(event.get("operation_id") or "").strip()
    if not operation_id:
        raise ValueError("Outbox event has no operation_id")

    payload_raw = event.get("payload")
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    if not isinstance(payload, dict):
        raise ValueError("Outbox payload must be a JSON object")

    entity_type = event.get("entity_type")
    if entity_type != "lab_progress":
        raise ValueError(f"Unsupported sync entity_type: {entity_type!r}")

    student_id = str(payload.get("student_id") or "").strip()
    if not student_id:
        raise ValueError("lab_progress event is missing student_id")
    try:
        lab_number = int(payload["lab_number"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("lab_progress event has an invalid lab_number") from exc
    if lab_number < 1:
        raise ValueError("lab_number must be positive")

    incoming_steps = _normalise_steps(payload.get("steps_completed", []))
    incoming_verified = 1 if bool(int(payload.get("verification_passed", 0) or 0)) else 0
    incoming_completed_at = payload.get("completed_at")
    incoming_updated_at = payload.get("updated_at")

    try:
        # The unique operation_id is committed atomically with the progress update.
        marker = cloud.execute(
            """
            INSERT INTO processed_sync_events (operation_id)
            VALUES (?)
            ON CONFLICT (operation_id) DO NOTHING
            RETURNING operation_id
            """,
            (operation_id,),
        ).fetchone()

        if marker is None:
            # This operation was already committed remotely; safe to acknowledge locally.
            cloud.commit()
            return

        current = cloud.execute(
            """
            SELECT steps_completed, verification_passed, completed_at, updated_at
            FROM lab_progress
            WHERE student_id = ? AND lab_number = ?
            FOR UPDATE
            """,
            (student_id, lab_number),
        ).fetchone()

        if current:
            current_steps = _normalise_steps(_row_value(current, "steps_completed", 0))
            merged_steps = sorted(set(current_steps).union(incoming_steps))
            current_verified = int(_row_value(current, "verification_passed", 1) or 0)
            current_completed_at = _row_value(current, "completed_at", 2)
            current_updated_at = _row_value(current, "updated_at", 3)
            verified = max(current_verified, incoming_verified)
            completed_at = current_completed_at or incoming_completed_at
            updated_at = max(
                [str(v) for v in (current_updated_at, incoming_updated_at) if v is not None],
                default=None,
            )
        else:
            merged_steps = incoming_steps
            verified = incoming_verified
            completed_at = incoming_completed_at
            updated_at = incoming_updated_at

        if not updated_at:
            cloud.execute(
                """
                INSERT INTO lab_progress
                    (student_id, lab_number, steps_completed, verification_passed, completed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (student_id, lab_number) DO UPDATE SET
                    steps_completed = EXCLUDED.steps_completed,
                    verification_passed = EXCLUDED.verification_passed,
                    completed_at = COALESCE(lab_progress.completed_at, EXCLUDED.completed_at),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (student_id, lab_number, json.dumps(merged_steps), verified, completed_at),
            )
        else:
            cloud.execute(
                """
                INSERT INTO lab_progress
                    (student_id, lab_number, steps_completed, verification_passed, completed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (student_id, lab_number) DO UPDATE SET
                    steps_completed = EXCLUDED.steps_completed,
                    verification_passed = EXCLUDED.verification_passed,
                    completed_at = COALESCE(lab_progress.completed_at, EXCLUDED.completed_at),
                    updated_at = GREATEST(lab_progress.updated_at, EXCLUDED.updated_at)
                """,
                (student_id, lab_number, json.dumps(merged_steps), verified, completed_at, updated_at),
            )
        cloud.commit()
    except Exception:
        cloud.rollback()
        raise


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCK_FILE_PATH = os.path.join(BASE_DIR, "database", ".sync_worker.lock")


class ProcessWorkerLock:
    """
    Cross-platform leader lease / lock for multi-worker environments (e.g. Gunicorn with multiple workers).
    Guarantees only one worker process acquires the sync lease and polls the outbox, eliminating contention.
    """
    def __init__(self, lock_path: str = LOCK_FILE_PATH) -> None:
        self.lock_path = lock_path
        self._fd: Optional[Any] = None
        self._locked = False

    def acquire(self) -> bool:
        if self._locked and self._fd:
            return True
        try:
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
            self._fd = open(self.lock_path, "a+")
            if os.name == "nt":
                import msvcrt
                self._fd.seek(0)
                msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._locked = True
            return True
        except (IOError, OSError):
            if self._fd:
                try:
                    self._fd.close()
                except Exception:
                    pass
                self._fd = None
            self._locked = False
            return False

    def release(self) -> None:
        if self._fd and self._locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    self._fd.seek(0)
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
                self._fd.close()
            except Exception:
                pass
            self._fd = None
            self._locked = False

    @property
    def is_locked(self) -> bool:
        return self._locked


class CloudSyncService:
    """Lifecycle-managed polling worker with capped exponential backoff and jitter."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._process_lock = ProcessWorkerLock()
        self._running = False
        self._last_error: Optional[str] = None
        self._last_success_at: Optional[float] = None
        self._last_attempt_at: Optional[float] = None
        self._last_synced_count = 0

    def start_worker(self) -> None:
        """Start once per process; safe for repeated calls."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="vlab-cloud-sync",
                daemon=True,
            )
            self._thread.start()

    def stop_worker(self, timeout: float = 5.0) -> None:
        """Request a clean stop (primarily for tests and controlled shutdown)."""
        self._stop_event.set()
        self._process_lock.release()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    @property
    def status(self) -> Dict[str, Any]:
        thread = self._thread
        return {
            "configured": bool(os.environ.get("DATABASE_URL", "").strip()),
            "worker_running": bool(thread and thread.is_alive()),
            "leader_active": self._process_lock.is_locked,
            "last_error": self._last_error,
            "last_success_at": self._last_success_at,
            "last_attempt_at": self._last_attempt_at,
            "last_synced_count": self._last_synced_count,
        }

    def sync_once(self) -> int:
        """Run one batch; returns the number of locally acknowledged events."""
        self._last_error = None
        events = get_pending_sync_events(limit=BATCH_SIZE)
        if not events:
            self._last_synced_count = 0
            return 0

        self._last_attempt_at = time.time()
        cloud = get_cloud_db()
        if cloud is None:
            message = "Cloud PostgreSQL is not configured or unreachable."
            self._last_error = message
            for event in events:
                record_sync_event_failure(str(event["operation_id"]), message)
            raise ConnectionError(message)

        synced = 0
        failures = 0
        try:
            ensure_cloud_schema(cloud)
            for event in events:
                operation_id = str(event["operation_id"])
                try:
                    apply_lab_progress_event(cloud, event)
                    # Only acknowledge locally after the cloud transaction committed.
                    if mark_sync_event_synced(operation_id):
                        synced += 1
                except Exception as exc:
                    failures += 1
                    message = f"{type(exc).__name__}: {exc}"
                    record_sync_event_failure(operation_id, message)
                    self._last_error = message
            self._last_synced_count = synced
            if failures:
                raise RuntimeError(f"{failures} outbox event(s) failed to synchronize.")
            self._last_success_at = time.time()
            return synced
        finally:
            cloud.close()

    def _worker_loop(self) -> None:
        backoff = BASE_INTERVAL_SECONDS
        while not self._stop_event.is_set():
            # In multi-worker environments (e.g. Gunicorn -w 4), only the elected leader process polls
            if not self._process_lock.acquire():
                # Another worker process holds the lease; sleep and retry quietly
                self._stop_event.wait(BASE_INTERVAL_SECONDS)
                continue

            try:
                self.sync_once()
                backoff = BASE_INTERVAL_SECONDS
                wait_for = BASE_INTERVAL_SECONDS
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                wait_for = min(backoff, MAX_BACKOFF_SECONDS)
                # Full jitter avoids synchronized retry storms across multiple hosts.
                wait_for = random.uniform(max(1.0, wait_for / 2.0), wait_for)
                backoff = min(backoff * 2.0, MAX_BACKOFF_SECONDS)
            self._stop_event.wait(wait_for)


cloud_sync_service = CloudSyncService()


if __name__ == "__main__":
    import sys
    import signal
    print("[V-Lab Sync Service] Starting standalone background sync worker...")
    cloud_sync_service.start_worker()

    def _sig_handler(sig, frame):
        print("\n[V-Lab Sync Service] Shutting down cleanly...")
        cloud_sync_service.stop_worker()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sig_handler)

    print("[V-Lab Sync Service] Worker active. Press Ctrl+C to terminate.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cloud_sync_service.stop_worker()
        print("[V-Lab Sync Service] Terminated.")
