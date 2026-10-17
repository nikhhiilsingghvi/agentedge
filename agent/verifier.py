"""
agent/verifier.py — Closed-Loop State Verification (Novel Contribution #3)
==========================================================================

Performs post-execution verification by comparing the expected actuator
state (derived from each executed plan step) against the observed actuator
state (queried from ActuatorSimulator).

SIMULATION BOUNDARY
-------------------
All verification in this module is performed against the ActuatorSimulator's
in-memory state dictionary. This confirms that the software path correctly
drove the actuator model. It does NOT verify physical hardware state (GPIO
pin voltage, relay coil continuity, etc.). This distinction must be clearly
stated in any publication citing closed-loop verification.

Usage:
    verifier = Verifier()
    report = verifier.verify_plan(plan, exec_result, actuator_sim)
    if not report.overall_success:
        for failure in report.verification_failures:
            logger.error("Verification failed: %s", failure)
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any

from core.logger import get_logger

logger = get_logger("Verifier")


class VerificationMethod(Enum):
    """Describes what was compared in a verification check."""
    ACTUATOR_STATE = "ACTUATOR_STATE"  # Commanded state vs observed actuator state
    ALERT_LOG      = "ALERT_LOG"       # Alert expected → alert log entry present
    NOT_APPLICABLE = "NOT_APPLICABLE"  # Step type (LOG, WAIT, VERIFY) has no state to check


class VerificationStatus(Enum):
    PASS    = "PASS"
    FAIL    = "FAIL"
    SKIPPED = "SKIPPED"   # Step type doesn't produce verifiable state


# Map of plan step commands → expected ActuatorState string
_COMMAND_TO_EXPECTED_STATE: Dict[str, str] = {
    "ON":    "ON",
    "OFF":   "OFF",
    "ALERT": "ON",   # BLE is set to ON after a successful ALERT command
    "BLINK": "ON",   # LED is transiently ON during BLINK
}


@dataclass
class VerificationResult:
    """
    Result of verifying a single plan step.

    Fields:
        plan_id         : Parent plan identifier
        step_id         : Plan step identifier
        actuator_id     : Target actuator (or '' for non-actuator steps)
        expected_state  : What state the actuator should be in post-execution
        observed_state  : What state the actuator reports post-execution
        status          : PASS / FAIL / SKIPPED
        method          : Verification method used
        notes           : Human-readable explanation
        latency_ms      : Time taken to perform this verification check
        timestamp       : Unix timestamp of the check
        is_simulation   : Always True — physical verification not performed
    """
    plan_id:        str
    step_id:        str
    actuator_id:    str
    expected_state: Optional[str]
    observed_state: Optional[str]
    status:         VerificationStatus
    method:         VerificationMethod
    notes:          str
    latency_ms:     float
    timestamp:      float
    is_simulation:  bool = True

    def to_dict(self) -> dict:
        return {
            "plan_id":        self.plan_id,
            "step_id":        self.step_id,
            "actuator_id":    self.actuator_id,
            "expected_state": self.expected_state,
            "observed_state": self.observed_state,
            "status":         self.status.value,
            "method":         self.method.value,
            "notes":          self.notes,
            "latency_ms":     round(self.latency_ms, 3),
            "timestamp":      self.timestamp,
            "is_simulation":  self.is_simulation,
        }


@dataclass
class VerificationReport:
    """
    Aggregate result of verifying all steps in a plan.

    Fields:
        plan_id               : Plan identifier
        total_steps_checked   : Number of steps that were verifiable
        passed                : Count of PASS results
        failed                : Count of FAIL results
        skipped               : Count of SKIPPED results (non-verifiable steps)
        overall_success       : True iff failed == 0 and total_steps_checked > 0
        verification_failures : List of step_id strings that failed
        results               : Ordered list of VerificationResult objects
        total_latency_ms      : Sum of all check latencies
        is_simulation         : Always True
    """
    plan_id:               str
    total_steps_checked:   int
    passed:                int
    failed:                int
    skipped:               int
    overall_success:       bool
    verification_failures: List[str]
    results:               List[VerificationResult]
    total_latency_ms:      float
    is_simulation:         bool = True

    def to_dict(self) -> dict:
        return {
            "plan_id":               self.plan_id,
            "total_steps_checked":   self.total_steps_checked,
            "passed":                self.passed,
            "failed":                self.failed,
            "skipped":               self.skipped,
            "overall_success":       self.overall_success,
            "verification_failures": self.verification_failures,
            "total_latency_ms":      round(self.total_latency_ms, 3),
            "is_simulation":         self.is_simulation,
            "results":               [r.to_dict() for r in self.results],
        }


class Verifier:
    """
    Closed-Loop State Verifier (Novel Contribution #3).

    For each ACTUATOR or ALERT step in the executed plan:
      1. Derive the expected actuator state from the step command.
      2. Query the actuator simulator for the observed state.
      3. Compare; produce a VerificationResult.

    Steps of type LOG, WAIT, VERIFY are skipped (SKIPPED status).
    Failed steps (outcome=FAILED in ExecutionResult) are checked but noted
    as incomplete — their verification is degraded, not skipped.
    """

    def verify_plan(
        self,
        plan,                  # AgentPlan
        exec_result,           # ExecutionResult
        actuator_sim,          # ActuatorSimulator
    ) -> VerificationReport:
        """
        Verify all steps in the plan against the actuator simulator state.

        Args:
            plan         : The AgentPlan that was executed
            exec_result  : ExecutionResult from the executor
            actuator_sim : ActuatorSimulator instance to query state from

        Returns:
            VerificationReport with per-step results and aggregate pass/fail
        """
        t_report_start = time.time()
        results: List[VerificationResult] = []

        # Build a map of step_id → step execution outcome for failed-step annotation
        failed_step_ids = set()
        for sr in exec_result.step_results:
            from agent.executor import StepOutcome
            if sr.outcome == StepOutcome.FAILED:
                # Normalise split step IDs (e.g. "S1_SPLIT" → "S1")
                base_id = sr.step_id.replace("_SPLIT", "").replace("_WARN", "")
                failed_step_ids.add(base_id)

        all_states = actuator_sim.get_all_states()

        for step in plan.steps:
            t0 = time.time()

            if step.action_type not in ("ACTUATOR", "ALERT"):
                # Non-verifiable step type
                results.append(VerificationResult(
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    actuator_id="",
                    expected_state=None,
                    observed_state=None,
                    status=VerificationStatus.SKIPPED,
                    method=VerificationMethod.NOT_APPLICABLE,
                    notes=f"Step type {step.action_type!r} has no verifiable actuator state",
                    latency_ms=(time.time() - t0) * 1000,
                    timestamp=time.time(),
                ))
                continue

            # Determine target actuator
            if step.action_type == "ALERT":
                actuator_id = "ble"
                method = VerificationMethod.ALERT_LOG
            else:
                actuator_id = step.target.lower()
                method = VerificationMethod.ACTUATOR_STATE

            expected_state = _COMMAND_TO_EXPECTED_STATE.get(step.command.upper())
            observed_state = all_states.get(actuator_id)

            # Was this step marked as FAILED by the executor?
            step_failed = step.step_id in failed_step_ids

            if expected_state is None:
                # Unknown command type — cannot verify
                results.append(VerificationResult(
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    actuator_id=actuator_id,
                    expected_state=None,
                    observed_state=observed_state,
                    status=VerificationStatus.SKIPPED,
                    method=method,
                    notes=f"Unknown command {step.command!r} — cannot derive expected state",
                    latency_ms=(time.time() - t0) * 1000,
                    timestamp=time.time(),
                ))
                continue

            if observed_state is None:
                # Actuator not found in simulator
                results.append(VerificationResult(
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    actuator_id=actuator_id,
                    expected_state=expected_state,
                    observed_state=None,
                    status=VerificationStatus.FAIL,
                    method=method,
                    notes=f"Actuator {actuator_id!r} not found in simulator state",
                    latency_ms=(time.time() - t0) * 1000,
                    timestamp=time.time(),
                ))
                continue

            passed = (observed_state.upper() == expected_state.upper())
            status = VerificationStatus.PASS if passed else VerificationStatus.FAIL

            if step_failed:
                notes = (
                    f"[EXECUTOR FAILED] Step did not execute — "
                    f"state mismatch expected: {expected_state}, observed: {observed_state}"
                )
                status = VerificationStatus.FAIL
            elif passed:
                notes = (
                    f"[SIMULATION] {actuator_id} in expected state {expected_state} "
                    f"(observed: {observed_state})"
                )
            else:
                notes = (
                    f"[SIMULATION] State mismatch — "
                    f"expected {expected_state}, observed {observed_state}"
                )

            results.append(VerificationResult(
                plan_id=plan.plan_id,
                step_id=step.step_id,
                actuator_id=actuator_id,
                expected_state=expected_state,
                observed_state=observed_state,
                status=status,
                method=method,
                notes=notes,
                latency_ms=(time.time() - t0) * 1000,
                timestamp=time.time(),
            ))

        # Aggregate
        passed_count  = sum(1 for r in results if r.status == VerificationStatus.PASS)
        failed_count  = sum(1 for r in results if r.status == VerificationStatus.FAIL)
        skipped_count = sum(1 for r in results if r.status == VerificationStatus.SKIPPED)
        checked_count = passed_count + failed_count
        failures      = [r.step_id for r in results if r.status == VerificationStatus.FAIL]

        overall_success = (failed_count == 0 and checked_count > 0)
        total_latency   = (time.time() - t_report_start) * 1000

        level = logger.info if overall_success else logger.warning
        level(
            "[verifier] Plan %s | ✅ %d passed | ❌ %d failed | ⏭ %d skipped | %.1f ms [SIMULATION]",
            plan.plan_id, passed_count, failed_count, skipped_count, total_latency,
        )

        return VerificationReport(
            plan_id=plan.plan_id,
            total_steps_checked=checked_count,
            passed=passed_count,
            failed=failed_count,
            skipped=skipped_count,
            overall_success=overall_success,
            verification_failures=failures,
            results=results,
            total_latency_ms=total_latency,
        )
