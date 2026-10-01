"""Session recording and comparison (spec §33, §83, §84, §103).

A *session* is a bounded recording: it accumulates statistics from the live
telemetry stream, stores its own sample series, and can be compared with another
session or a saved baseline.

Sessions are interrupted, never lost: a session left ``RUNNING`` by an unclean
shutdown is finalised as ``INTERRUPTED`` the next time the application starts
(spec §83).
"""

from __future__ import annotations

import statistics
import uuid

from app.core.clock import utc_now
from app.core.enums import DataProvenance, SessionState
from app.core.events import EventBus
from app.core.logging_setup import get_logger
from app.models.sessions import Baseline, CoolingSession, SessionComparison
from app.models.telemetry import TelemetrySnapshot
from app.persistence.repositories import BaselineRepository, SessionRepository

_log = get_logger(__name__)

TOPIC_SESSIONS = "sessions.changed"

#: Store at most one sample per this many seconds while recording.
SAMPLE_INTERVAL_S = 1.0


class SessionRecorder:
    """Records cooling sessions from the live telemetry stream."""

    def __init__(
        self,
        sessions: SessionRepository,
        baselines: BaselineRepository,
        bus: EventBus,
    ) -> None:
        self._sessions = sessions
        self._baselines = baselines
        self._bus = bus
        self._current: CoolingSession | None = None
        self._cpu: list[float] = []
        self._gpu: list[float] = []
        self._rpm: list[float] = []
        self._last_sample_at: float = 0.0

    # -- queries -----------------------------------------------------------

    @property
    def current(self) -> CoolingSession | None:
        return self._current

    @property
    def recording(self) -> bool:
        return self._current is not None and self._current.state is SessionState.RUNNING

    def recent(self, limit: int = 50) -> list[CoolingSession]:
        return self._sessions.list_recent(limit)

    def baselines(self) -> list[Baseline]:
        return self._baselines.list_all()

    # -- lifecycle ---------------------------------------------------------

    def start(self, name: str = "", *, profile_key: str = "", simulated: bool = False) -> CoolingSession:
        if self.recording:
            raise RuntimeError("a session is already being recorded")
        session = CoolingSession(
            session_id=uuid.uuid4().hex[:12],
            name=name or f"Session {utc_now().strftime('%Y-%m-%d %H:%M')}",
            state=SessionState.RUNNING,
            profile_key=profile_key,
            simulated=simulated,
            provenance=DataProvenance.SIMULATED if simulated else DataProvenance.MEASURED,
        )
        self._current = session
        self._cpu.clear()
        self._gpu.clear()
        self._rpm.clear()
        self._last_sample_at = 0.0
        self._sessions.upsert(session)
        self._bus.publish(TOPIC_SESSIONS, {"started": session.session_id})
        _log.info("session started: %s", session.session_id)
        return session

    def observe(self, snapshot: TelemetrySnapshot) -> None:
        """Accumulate one snapshot into the running session."""
        session = self._current
        if session is None or session.state is not SessionState.RUNNING:
            return

        cpu = snapshot.cpu.temperature
        gpu = snapshot.gpu.temperature
        rpm = snapshot.fan.rpm
        if cpu is not None:
            self._cpu.append(cpu)
        if gpu is not None:
            self._gpu.append(gpu)
        if rpm is not None:
            self._rpm.append(rpm)

        session.sample_count += 1
        session.esp32_connected = session.esp32_connected or snapshot.fan.available
        if cpu is not None:
            session.max_cpu_c = _max(session.max_cpu_c, cpu)
        if gpu is not None:
            session.max_gpu_c = _max(session.max_gpu_c, gpu)
        if rpm is not None:
            session.peak_rpm = _max(session.peak_rpm, rpm)
        power = snapshot.fan.power_w
        if power is not None:
            session.max_fan_power_w = _max(session.max_fan_power_w, power)

        timestamp = snapshot.timestamp.timestamp()
        session_id = session.session_id
        if (
            session_id is not None
            and timestamp - self._last_sample_at >= SAMPLE_INTERVAL_S
        ):
            self._last_sample_at = timestamp
            self._sessions.add_sample(session_id, snapshot)

    def sync_statistics(self, *, time_above_threshold_s: float) -> None:
        """Fold running aggregates into the session (called periodically)."""
        session = self._current
        if session is None:
            return
        session.avg_cpu_c = _mean(self._cpu)
        session.avg_gpu_c = _mean(self._gpu)
        session.avg_rpm = _mean(self._rpm)
        session.time_above_threshold_s = time_above_threshold_s
        self._sessions.upsert(session)

    def stop(self, *, interrupted: bool = False) -> CoolingSession | None:
        session = self._current
        if session is None:
            return None
        session.state = SessionState.INTERRUPTED if interrupted else SessionState.COMPLETED
        session.ended_at = utc_now()
        session.avg_cpu_c = _mean(self._cpu)
        session.avg_gpu_c = _mean(self._gpu)
        session.avg_rpm = _mean(self._rpm)
        self._sessions.upsert(session)
        self._current = None
        self._bus.publish(TOPIC_SESSIONS, {"stopped": session.session_id})
        _log.info("session %s: %s", session.session_id, session.state.value)
        return session

    def recover_interrupted(self) -> int:
        """Finalise sessions left RUNNING by an unclean shutdown."""
        recovered = 0
        for session in self._sessions.list_recent(limit=200):
            if session.state is SessionState.RUNNING:
                session.state = SessionState.INTERRUPTED
                session.ended_at = session.ended_at or utc_now()
                session.notes = (session.notes + " [recovered after unclean shutdown]").strip()
                self._sessions.upsert(session)
                recovered += 1
        if recovered:
            _log.warning("recovered %d interrupted session(s)", recovered)
        return recovered

    # -- comparison --------------------------------------------------------

    def compare(self, left_id: str, right_id: str) -> SessionComparison | None:
        left = self._sessions.get(left_id)
        right = self._sessions.get(right_id)
        if left is None or right is None:
            return None
        return SessionComparison(left=left, right=right)

    def save_baseline(self, session_id: str, name: str) -> Baseline | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        baseline = Baseline(
            key=session_id,
            name=name,
            avg_cpu_c=session.avg_cpu_c,
            avg_gpu_c=session.avg_gpu_c,
            avg_fan_rpm=session.avg_rpm,
            sample_count=session.sample_count,
        )
        self._baselines.upsert(baseline)
        return baseline

    def delete(self, session_id: str) -> None:
        self._sessions.delete(session_id)


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def _max(current: float | None, value: float) -> float:
    return value if current is None else max(current, value)


__all__ = ["SessionRecorder", "TOPIC_SESSIONS", "SAMPLE_INTERVAL_S"]
