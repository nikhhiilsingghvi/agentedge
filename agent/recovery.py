"""
AgentEdge Recovery Protocol
============================
NOVEL CONTRIBUTION #2: Sensor Failure Recovery Protocol

Formal specification of agent behavior when a sensor fails
mid-task. This is the first published recovery protocol for
embedded agentic AI systems.

Recovery taxonomy:
  Type 1 — TIMEOUT:      Sensor did not respond in time
  Type 2 — BAD_DATA:     Sensor responded but data is invalid
  Type 3 — DISCONNECT:   Sensor physically disconnected
  Type 4 — BUS_ERROR:    I2C/SPI communication error

Recovery actions (in priority order):
  Action A — RETRY:      Retry read up to MAX_RECOVERY_ATTEMPTS
  Action B — SUBSTITUTE: Use last known good value with decay
  Action C — SKIP:       Skip sensor, log, continue with remaining
  Action D — ABORT:      Abort current task, log incident, safe-state

Patent-pending: The formal classification of embedded sensor
failures and the hierarchical recovery action selection method
for embedded agentic AI systems.

Copyright (c) 2026 AgentEdge Project, DSCE ECE Dept.
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Callable, Any
from enum import Enum

from config.settings import MAX_RECOVERY_ATTEMPTS, RECOVERY_WAIT_SECONDS
from core.logger import get_logger

logger = get_logger("RecoveryProtocol")


# ─── FAILURE TAXONOMY ─────────────────────────────────────────────────────────

class FailureType(Enum):
    TIMEOUT     = "TIMEOUT"      # No response within deadline
    BAD_DATA    = "BAD_DATA"     # Response received but invalid
    DISCONNECT  = "DISCONNECT"   # Physical disconnection detected
    BUS_ERROR   = "BUS_ERROR"    # I2C/SPI bus level error
    UNKNOWN     = "UNKNOWN"      # Unclassified failure


class RecoveryAction(Enum):
    RETRY       = "RETRY"        # Re-attempt sensor read
    SUBSTITUTE  = "SUBSTITUTE"   # Use last known good value
    SKIP        = "SKIP"         # Skip sensor, continue task
    ABORT       = "ABORT"        # Abort task, enter safe state


class RecoveryOutcome(Enum):
    SUCCESS     = "SUCCESS"      # Recovery succeeded
    PARTIAL     = "PARTIAL"      # Partial recovery — degraded mode
    FAILED      = "FAILED"       # Recovery failed — task aborted


# ─── DATA STRUCTURES ──────────────────────────────────────────────────────────

@dataclass
class FailureEvent:
    """Structured record of a sensor failure event."""
    sensor_id:      str
    failure_type:   FailureType
    failure_reason: str
    timestamp:      float
    task_id:        Optional[str] = None
    step_id:        Optional[str] = None


@dataclass
class RecoveryRecord:
    """Complete record of a recovery attempt — stored for paper analysis."""
    failure_event:      FailureEvent
    action_taken:       RecoveryAction
    attempts:           int
    outcome:            RecoveryOutcome
    recovered_value:    Optional[float]
    recovery_latency_ms: float
    notes:              str
    timestamp:          float

    def to_dict(self) -> dict:
        return {
            "sensor_id":          self.failure_event.sensor_id,
            "failure_type":       self.failure_event.failure_type.value,
            "failure_reason":     self.failure_event.failure_reason,
            "action_taken":       self.action_taken.value,
            "attempts":           self.attempts,
            "outcome":            self.outcome.value,
            "recovered_value":    self.recovered_value,
            "recovery_latency_ms": round(self.recovery_latency_ms, 2),
            "notes":              self.notes,
            "timestamp":          self.timestamp,
        }


# ─── RECOVERY PROTOCOL ENGINE ─────────────────────────────────────────────────

class RecoveryProtocol:
    """
    NOVEL CONTRIBUTION #2 — Sensor Failure Recovery Protocol.

    Implements a formal, hierarchical recovery process:

    Step 1: Classify failure type from failure_reason string
    Step 2: Select recovery action based on failure type + sensor criticality
    Step 3: Execute recovery action with retry loop
    Step 4: Evaluate outcome and decide: continue | degrade | abort
    Step 5: Log complete RecoveryRecord for paper analysis

    Sensor criticality levels:
      CRITICAL: mpu6050, ina219  (safety-critical — abort if unrecoverable)
      HIGH:     dht22            (important — substitute if unrecoverable)
      MEDIUM:   mq135, hcsr04   (useful — skip if unrecoverable)

    This hierarchy is the novel contribution — no published paper
    has defined sensor criticality-based recovery for embedded agents.
    """

    SENSOR_CRITICALITY: Dict[str, str] = {
        "mpu6050": "CRITICAL",   # Vibration — bearing failure detection
        "ina219":  "CRITICAL",   # Current — overcurrent = fire risk
        "dht22":   "HIGH",       # Temperature — overheating detection
        "hcsr04":  "MEDIUM",     # Distance — conveyor jam detection
        "mq135":   "MEDIUM",     # Gas — fume detection
    }

    # Failure type → preferred recovery action mapping
    RECOVERY_MATRIX: Dict[str, Dict[str, RecoveryAction]] = {
        "CRITICAL": {
            FailureType.TIMEOUT.value:    RecoveryAction.RETRY,
            FailureType.BAD_DATA.value:   RecoveryAction.RETRY,
            FailureType.DISCONNECT.value: RecoveryAction.ABORT,
            FailureType.BUS_ERROR.value:  RecoveryAction.RETRY,
            FailureType.UNKNOWN.value:    RecoveryAction.RETRY,
        },
        "HIGH": {
            FailureType.TIMEOUT.value:    RecoveryAction.RETRY,
            FailureType.BAD_DATA.value:   RecoveryAction.SUBSTITUTE,
            FailureType.DISCONNECT.value: RecoveryAction.SUBSTITUTE,
            FailureType.BUS_ERROR.value:  RecoveryAction.RETRY,
            FailureType.UNKNOWN.value:    RecoveryAction.SUBSTITUTE,
        },
        "MEDIUM": {
            FailureType.TIMEOUT.value:    RecoveryAction.RETRY,
            FailureType.BAD_DATA.value:   RecoveryAction.SKIP,
            FailureType.DISCONNECT.value: RecoveryAction.SKIP,
            FailureType.BUS_ERROR.value:  RecoveryAction.RETRY,
            FailureType.UNKNOWN.value:    RecoveryAction.SKIP,
        },
    }

    def __init__(self):
        self._last_known_good: Dict[str, float] = {}
        self._last_known_timestamps: Dict[str, float] = {}
        self._recovery_history: list = []
        self._abort_count: int = 0
        self._recovery_count: int = 0
        logger.info("[recovery] Recovery Protocol initialized")

    def update_last_known_good(self, sensor_id: str, value: float) -> None:
        """
        Called after every successful sensor read.
        Maintains the substitute value pool for Type B recovery.
        """
        self._last_known_good[sensor_id] = value
        self._last_known_timestamps[sensor_id] = time.time()

    def classify_failure(self, failure_reason: Optional[str]) -> FailureType:
        """
        Classify failure type from sensor failure_reason string.
        Maps human-readable error messages to formal taxonomy.
        """
        if not failure_reason:
            return FailureType.UNKNOWN

        r = failure_reason.lower()

        if "timeout" in r or "no response" in r or "no echo" in r:
            return FailureType.TIMEOUT
        elif "checksum" in r or "bad data" in r or "out of range" in r or "invalid" in r:
            return FailureType.BAD_DATA
        elif "disconnect" in r or "not found" in r or "address" in r:
            return FailureType.DISCONNECT
        elif "bus" in r or "i2c" in r or "spi" in r or "conflict" in r:
            return FailureType.BUS_ERROR
        else:
            return FailureType.UNKNOWN

    def recover(
        self,
        sensor_id: str,
        failure_reason: Optional[str],
        read_fn: Callable,
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
    ) -> RecoveryRecord:
        """
        NOVEL CONTRIBUTION #2 — Core recovery method.

        Args:
            sensor_id:      ID of the failed sensor
            failure_reason: Error string from SensorReading.failure_reason
            read_fn:        Callable that returns a fresh SensorReading
            task_id:        Current task ID (for logging)
            step_id:        Current step ID (for logging)

        Returns:
            RecoveryRecord — complete record of what happened
        """
        t0 = time.time()

        # Step 1: Classify failure
        failure_type = self.classify_failure(failure_reason)
        criticality = self.SENSOR_CRITICALITY.get(sensor_id, "MEDIUM")

        failure_event = FailureEvent(
            sensor_id=sensor_id,
            failure_type=failure_type,
            failure_reason=failure_reason or "unknown",
            timestamp=t0,
            task_id=task_id,
            step_id=step_id,
        )

        logger.warning(
            "[recovery] 🔴 Sensor failure: %s | Type: %s | Criticality: %s",
            sensor_id, failure_type.value, criticality
        )

        # Step 2: Select recovery action from matrix
        action = self.RECOVERY_MATRIX[criticality][failure_type.value]
        logger.info("[recovery] Selected action: %s", action.value)

        # Step 3: Execute recovery action
        recovered_value = None
        outcome = RecoveryOutcome.FAILED
        attempts = 0
        notes = ""

        if action == RecoveryAction.RETRY:
            outcome, recovered_value, attempts, notes = self._execute_retry(
                sensor_id, read_fn, criticality
            )

        elif action == RecoveryAction.SUBSTITUTE:
            outcome, recovered_value, notes = self._execute_substitute(sensor_id)

        elif action == RecoveryAction.SKIP:
            outcome = RecoveryOutcome.PARTIAL
            notes = f"Sensor {sensor_id} skipped — task continues without it"
            logger.warning("[recovery] ⏭️  Skipping sensor %s — continuing", sensor_id)

        elif action == RecoveryAction.ABORT:
            outcome = RecoveryOutcome.FAILED
            notes = f"ABORT: Critical sensor {sensor_id} disconnected — entering safe state"
            self._abort_count += 1
            logger.error("[recovery] 🚨 ABORT — %s", notes)

        # Step 4: Record
        recovery_latency_ms = (time.time() - t0) * 1000
        record = RecoveryRecord(
            failure_event=failure_event,
            action_taken=action,
            attempts=attempts,
            outcome=outcome,
            recovered_value=recovered_value,
            recovery_latency_ms=recovery_latency_ms,
            notes=notes,
            timestamp=time.time(),
        )
        self._recovery_history.append(record.to_dict())

        if outcome == RecoveryOutcome.SUCCESS:
            self._recovery_count += 1
            logger.info("[recovery] ✅ Recovery SUCCESS — %.1f ms", recovery_latency_ms)
        elif outcome == RecoveryOutcome.PARTIAL:
            logger.warning("[recovery] ⚡ Recovery PARTIAL — %.1f ms", recovery_latency_ms)
        else:
            logger.error("[recovery] ❌ Recovery FAILED — %.1f ms", recovery_latency_ms)

        return record

    def _execute_retry(
        self,
        sensor_id: str,
        read_fn: Callable,
        criticality: str,
    ):
        """Retry sensor read up to MAX_RECOVERY_ATTEMPTS times."""
        for attempt in range(1, MAX_RECOVERY_ATTEMPTS + 1):
            logger.info("[recovery] Retry attempt %d/%d for %s",
                        attempt, MAX_RECOVERY_ATTEMPTS, sensor_id)
            time.sleep(RECOVERY_WAIT_SECONDS)

            reading = read_fn()
            if not reading.is_failed and reading.value is not None:
                self.update_last_known_good(sensor_id, reading.value)
                return (
                    RecoveryOutcome.SUCCESS,
                    reading.value,
                    attempt,
                    f"Retry succeeded on attempt {attempt}",
                )

        # All retries failed — fall back based on criticality
        if criticality == "CRITICAL":
            return (
                RecoveryOutcome.FAILED,
                None,
                MAX_RECOVERY_ATTEMPTS,
                f"All {MAX_RECOVERY_ATTEMPTS} retries failed — ABORT required",
            )
        else:
            # Attempt substitute as fallback
            outcome, val, notes = self._execute_substitute(sensor_id)
            return outcome, val, MAX_RECOVERY_ATTEMPTS, notes

    def _execute_substitute(self, sensor_id: str):
        """Use last known good value with staleness check."""
        if sensor_id in self._last_known_good:
            age_s = time.time() - self._last_known_timestamps.get(sensor_id, 0)
            val = self._last_known_good[sensor_id]

            if age_s < 30.0:
                notes = f"Substituted last known good value {val:.3f} (age: {age_s:.1f}s)"
                logger.warning("[recovery] 🔄 Substitute: %s = %.3f (age %.1fs)",
                               sensor_id, val, age_s)
                return RecoveryOutcome.SUCCESS, val, notes
            else:
                notes = f"Last known good too stale ({age_s:.0f}s) — skipping sensor"
                logger.warning("[recovery] ⏭️  Substitute stale — skipping %s", sensor_id)
                return RecoveryOutcome.PARTIAL, None, notes
        else:
            notes = f"No last known good value for {sensor_id} — skipping"
            return RecoveryOutcome.PARTIAL, None, notes

    def get_statistics(self) -> dict:
        """Recovery statistics for paper's experimental results."""
        total = len(self._recovery_history)
        if total == 0:
            return {"total_failures": 0}

        by_type = {}
        by_action = {}
        by_outcome = {}

        for r in self._recovery_history:
            by_type[r["failure_type"]] = by_type.get(r["failure_type"], 0) + 1
            by_action[r["action_taken"]] = by_action.get(r["action_taken"], 0) + 1
            by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1

        latencies = [r["recovery_latency_ms"] for r in self._recovery_history]

        return {
            "total_failures":     total,
            "recovery_successes": self._recovery_count,
            "aborts":             self._abort_count,
            "success_rate_pct":   round(self._recovery_count / total * 100, 1),
            "avg_latency_ms":     round(sum(latencies) / len(latencies), 2),
            "by_failure_type":    by_type,
            "by_action":          by_action,
            "by_outcome":         by_outcome,
        }
