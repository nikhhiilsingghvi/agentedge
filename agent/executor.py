import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from agent.planner import AgentPlan, PlanStep
from core.resource_monitor import ResourceMonitor, ResourceState
from core.logger import get_logger

logger = get_logger("Executor")

class StepOutcome(Enum):
    SUCCESS  = "SUCCESS"
    SPLIT    = "SPLIT"
    FAILED   = "FAILED"
    SKIPPED  = "SKIPPED"

@dataclass
class StepResult:
    step_id:       str
    outcome:       StepOutcome
    actuator_results: list
    resource_snapshot: dict
    latency_ms:    float
    notes:         str
    timestamp:     float

    def to_dict(self) -> dict:
        return {
            "step_id":           self.step_id,
            "outcome":           self.outcome.value,
            "actuator_results":  [r.to_dict() if hasattr(r, 'to_dict') else r for r in self.actuator_results],
            "resource_snapshot": self.resource_snapshot,
            "latency_ms":        round(self.latency_ms, 2),
            "notes":             self.notes,
            "timestamp":         self.timestamp,
        }

@dataclass
class ExecutionResult:
    plan_id:       str
    total_steps:   int
    completed:     int
    split_count:   int
    failed_count:  int
    step_results:  List[StepResult]
    total_latency_ms: float
    success:       bool

    def to_dict(self) -> dict:
        return {
            "plan_id":         self.plan_id,
            "total_steps":     self.total_steps,
            "completed":       self.completed,
            "split_count":     self.split_count,
            "failed_count":    self.failed_count,
            "step_results":    [r.to_dict() for r in self.step_results],
            "total_latency_ms": round(self.total_latency_ms, 2),
            "success":         self.success,
        }


class Executor:
    """
    AgentEdge Plan Executor — Novel Contribution: Resource-Adaptive Decomposition (RAD)

    For each step in the plan:
      1. Check resource state before execution
      2. Route through 3-tier RAD policy:
           NORMAL   → full execution with complete payload
           WARN     → simplified payload (truncated, non-critical fields dropped)
           CRITICAL → core command only (payload=None)
      3. can_split=False steps ALWAYS execute at full tier regardless of resource state
         (safety-critical commands like relay:OFF must never be degraded)
      4. Execute step via actuator simulator
      5. Record result with latency and resource snapshot
    """

    def __init__(self, actuator_simulator, resource_monitor: ResourceMonitor):
        self._actuators = actuator_simulator
        self._resources = resource_monitor
        self._split_count = 0          # CRITICAL-tier decompositions
        self._warn_split_count = 0     # WARN-tier simplifications (new in v1.1)
        self._total_steps_executed = 0
        logger.info("[executor] Executor initialized — RAD v1.1 (3-tier)")

    def execute_plan(self, plan: AgentPlan) -> ExecutionResult:
        """Execute all steps in a plan with resource-adaptive decomposition."""
        t0 = time.time()
        step_results = []
        completed = 0
        failed_count = 0

        logger.info("[executor] ▶️  Executing plan %s | %d steps | Severity: %s",
                    plan.plan_id, len(plan.steps), plan.severity)

        for step in plan.steps:
            result = self._execute_step(step, plan.plan_id)
            step_results.append(result)
            self._total_steps_executed += 1

            if result.outcome in (StepOutcome.SUCCESS, StepOutcome.SPLIT):
                completed += 1
            elif result.outcome == StepOutcome.FAILED:
                failed_count += 1
                # For CRITICAL severity, abort on first failure
                if plan.severity == "FAULT":
                    logger.error("[executor] Aborting plan — FAULT severity + step failure")
                    break

        total_latency = (time.time() - t0) * 1000
        success = failed_count == 0

        logger.info(
            "[executor] Plan %s complete | ✅ %d/%d | ❌ %d | Split: %d | %.0f ms",
            plan.plan_id, completed, len(plan.steps),
            failed_count, self._split_count, total_latency,
        )

        return ExecutionResult(
            plan_id=plan.plan_id,
            total_steps=len(plan.steps),
            completed=completed,
            split_count=self._split_count,
            failed_count=failed_count,
            step_results=step_results,
            total_latency_ms=total_latency,
            success=success,
        )

    def _execute_step(self, step: PlanStep, plan_id: str) -> StepResult:
        """
        Execute a single step through the 3-tier RAD policy.

        Tier routing:
          can_split=False  → always FULL (safety-critical, never decomposed)
          NORMAL           → FULL
          WARN             → SIMPLIFIED PAYLOAD (truncate, keep semantics)
          CRITICAL         → CORE ONLY (payload=None)
        """
        t0 = time.time()

        # ── Resource check before step ───────────────────────────────────────
        self._resources.set_inference_active(False)
        res_state, snap = self._resources.check_before_step(step.description)

        # ── RAD Tier Routing ─────────────────────────────────────────────────
        if not step.can_split:
            # Safety-critical step: always execute at full tier, no decomposition
            logger.debug("[executor] Step %s: can_split=False — safety lock, full exec",
                         step.step_id)
            return self._execute_full(step, snap, t0)

        elif res_state == ResourceState.CRITICAL:
            # Tier 3: CRITICAL — core command only, no payload
            self._split_count += 1
            logger.warning(
                "[executor] 🔀 RAD CRITICAL-tier: step %s — RAM: %.1f%%",
                step.step_id, snap.ram_percent
            )
            return self._execute_split_step(step, snap, t0)

        elif res_state == ResourceState.WARN:
            # Tier 2: WARN — simplified payload, preserves alert semantics
            self._warn_split_count += 1
            logger.info(
                "[executor] 🔁 RAD WARN-tier: step %s — RAM: %.1f%%",
                step.step_id, snap.ram_percent
            )
            return self._execute_simplified_step(step, snap, t0)

        else:
            # Tier 1: NORMAL — full execution
            return self._execute_full(step, snap, t0)
    def _execute_full(self, step: PlanStep, snap, t0: float) -> StepResult:
        """Tier 1: Full execution — complete payload, all fields."""
        actuator_results = []
        notes = ""

        try:
            if step.action_type == "ACTUATOR":
                result = self._actuators.execute(
                    actuator_id=step.target,
                    command=step.command,
                    payload=step.payload,
                )
                actuator_results.append(result)
                notes = result.message

            elif step.action_type == "ALERT":
                result = self._actuators.execute(
                    actuator_id="ble",
                    command="ALERT",
                    payload=step.payload or {"message": step.description, "severity": "INFO"},
                )
                actuator_results.append(result)
                notes = result.message

            elif step.action_type == "LOG":
                notes = f"Logged: {step.description}"
                logger.info("[executor] 📝 LOG: %s", step.description)

            elif step.action_type == "VERIFY":
                notes = f"Verification step: {step.description}"
                logger.info("[executor] 🔍 VERIFY: %s", step.description)

            elif step.action_type == "WAIT":
                wait_s = (step.payload or {}).get("seconds", 1.0)
                time.sleep(wait_s)
                notes = f"Waited {wait_s}s"

            outcome = StepOutcome.SUCCESS
            logger.info("[executor] ✅ Step %s: %s", step.step_id, notes)

        except Exception as e:
            outcome = StepOutcome.FAILED
            notes = f"Execution error: {e}"
            logger.error("[executor] ❌ Step %s failed: %s", step.step_id, e)

        latency_ms = (time.time() - t0) * 1000
        return StepResult(
            step_id=step.step_id,
            outcome=outcome,
            actuator_results=actuator_results,
            resource_snapshot=snap.to_dict(),
            latency_ms=latency_ms,
            notes=notes,
            timestamp=time.time(),
        )

    def _execute_simplified_step(self, step: PlanStep, snap, t0: float) -> StepResult:
        """
        Tier 2 RAD (WARN): Simplified payload execution.
        Truncates long messages and drops optional verbose fields.
        Preserves command semantics and required alert content.
        """
        simplified_payload = self._simplify_payload(step.payload)
        logger.info("[executor] 🔁 WARN-tier simplified payload for step %s", step.step_id)
        actuator_results = []

        try:
            if step.action_type in ("ACTUATOR", "ALERT"):
                target = "ble" if step.action_type == "ALERT" else step.target
                command = "ALERT" if step.action_type == "ALERT" else step.command
                result = self._actuators.execute(
                    actuator_id=target,
                    command=command,
                    payload=simplified_payload,
                )
                actuator_results.append(result)
                outcome = StepOutcome.SPLIT
                notes = f"[WARN-tier] Simplified payload | RAM {snap.ram_percent:.1f}% | {result.message}"
            else:
                # LOG / VERIFY / WAIT always execute normally regardless of WARN state
                return self._execute_full(step, snap, t0)

        except Exception as e:
            outcome = StepOutcome.FAILED
            notes = f"WARN-tier execution failed: {e}"
            logger.error("[executor] ❌ WARN-tier step %s failed: %s", step.step_id, e)

        latency_ms = (time.time() - t0) * 1000
        return StepResult(
            step_id=f"{step.step_id}_WARN",
            outcome=outcome,
            actuator_results=actuator_results,
            resource_snapshot=snap.to_dict(),
            latency_ms=latency_ms,
            notes=notes,
            timestamp=time.time(),
        )

    @staticmethod
    def _simplify_payload(payload: Optional[dict]) -> Optional[dict]:
        """
        Reduce payload size for WARN-tier execution.
        Preserves required semantic fields (severity, message core).
        Truncates long message strings. Does NOT nullify the payload.
        """
        if not payload:
            return payload
        simplified = {}
        for key, val in payload.items():
            if key == "message" and isinstance(val, str) and len(val) > 80:
                simplified[key] = val[:77] + "..."
            elif key == "severity":
                simplified[key] = val        # Always preserve severity
            elif key in ("timestamp", "details", "stack_trace", "raw_data", "metadata"):
                pass                         # Drop optional/verbose fields
            else:
                simplified[key] = val
        return simplified or None

    def _execute_split_step(self, step: PlanStep, snap, t0: float) -> StepResult:
        """
        Tier 3 RAD (CRITICAL): Core-command-only execution.
        Strips payload entirely — only the essential actuator command is sent.
        This guarantees the most critical operation (e.g. relay:OFF) succeeds
        even when RAM is critically exhausted.
        """
        logger.info("[executor] 🔀 CRITICAL-tier core-only execution for step %s", step.step_id)

        actuator_results = []

        try:
            result = self._actuators.execute(
                actuator_id=step.target,
                command=step.command,
                payload=None,  # Core command only — payload entirely stripped
            )
            actuator_results.append(result)
            outcome = StepOutcome.SPLIT
            notes = f"[CRITICAL-tier] Core command only — RAM was {snap.ram_percent:.1f}% | {result.message}"
            logger.warning("[executor] 🔀 CRITICAL-tier split: %s", notes)

        except Exception as e:
            outcome = StepOutcome.FAILED
            notes = f"CRITICAL-tier execution failed: {e}"
            logger.error("[executor] ❌ CRITICAL-tier step %s failed: %s", step.step_id, e)

        latency_ms = (time.time() - t0) * 1000
        return StepResult(
            step_id=f"{step.step_id}_SPLIT",
            outcome=outcome,
            actuator_results=actuator_results,
            resource_snapshot=snap.to_dict(),
            latency_ms=latency_ms,
            notes=notes,
            timestamp=time.time(),
        )

    def get_statistics(self) -> dict:
        return {
            "total_steps_executed":  self._total_steps_executed,
            "total_splits_critical": self._split_count,
            "total_splits_warn":     self._warn_split_count,
            "total_rad_adaptations": self._split_count + self._warn_split_count,
            "rad_triggered":         (self._split_count + self._warn_split_count) > 0,
            "split_rate_pct":        round(
                (self._split_count + self._warn_split_count) /
                max(self._total_steps_executed, 1) * 100, 1
            ),
        }

    def reset_stats(self) -> None:
        """Reset execution statistics for benchmark task isolation."""
        self._split_count = 0
        self._warn_split_count = 0
        self._total_steps_executed = 0
        logger.debug("[executor] Execution stats reset")
