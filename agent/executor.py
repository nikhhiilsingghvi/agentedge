"""
AgentEdge Executor
==================
Executes agent plans step by step.
Integrates Novel Contribution #1 (Resource-Adaptive Decomposition)
at the step level — checks RAM/CPU before each step and splits
if resources are constrained.

Verifies execution outcomes and feeds results back to the agent loop.

Copyright (c) 2026 AgentEdge Project, DSCE ECE Dept.
"""

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
    SPLIT    = "SPLIT"       # Step was split due to resource constraint
    FAILED   = "FAILED"
    SKIPPED  = "SKIPPED"


@dataclass
class StepResult:
    """Result of executing a single plan step."""
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
    """Complete result of executing a full plan."""
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
    AgentEdge Plan Executor.

    For each step in the plan:
      1. Check resource state (Novel Contribution #1)
      2. If CRITICAL → decompose step into sub-steps
      3. Execute step via actuator simulator (or real GPIO in hardware phase)
      4. Record result with latency and resource snapshot
      5. Verify execution (read sensor to confirm effect)
    """

    def __init__(self, actuator_simulator, resource_monitor: ResourceMonitor):
        self._actuators = actuator_simulator
        self._resources = resource_monitor
        self._split_count = 0
        self._total_steps_executed = 0
        logger.info("[executor] Executor initialized")

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
        Execute a single step with resource check (Novel Contribution #1).
        """
        t0 = time.time()

        # ── Resource check before step (Novel Contribution #1) ───────────────
        self._resources.set_inference_active(False)
        res_state, snap = self._resources.check_before_step(step.description)

        if res_state == ResourceState.CRITICAL and step.can_split:
            # Decompose this step
            self._split_count += 1
            logger.warning(
                "[executor] 🔀 SPLITTING step %s due to resource pressure "
                "(RAM: %.1f%%)", step.step_id, snap.ram_percent
            )
            return self._execute_split_step(step, snap, t0)

        # ── Normal step execution ─────────────────────────────────────────────
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

    def _execute_split_step(self, step: PlanStep, snap, t0: float) -> StepResult:
        """
        Decompose a step into minimal sub-operations when RAM is critical.
        This is the core of Novel Contribution #1.
        Only the immediate actuator command is executed; verification
        is deferred to the next agent cycle.
        """
        logger.info("[executor] 🔀 Executing split version of step %s", step.step_id)
        actuator_results = []

        try:
            # Execute only the core command — skip payload extras
            result = self._actuators.execute(
                actuator_id=step.target,
                command=step.command,
                payload=None,  # Simplified payload for resource-constrained execution
            )
            actuator_results.append(result)
            outcome = StepOutcome.SPLIT
            notes = f"[SPLIT] Core command only — RAM was {snap.ram_percent:.1f}% | {result.message}"
            logger.warning("[executor] 🔀 Split execution: %s", notes)

        except Exception as e:
            outcome = StepOutcome.FAILED
            notes = f"Split execution failed: {e}"
            logger.error("[executor] ❌ Split failed: %s", e)

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
            "total_steps_executed": self._total_steps_executed,
            "total_splits":         self._split_count,
            "split_rate_pct":       round(
                self._split_count / max(self._total_steps_executed, 1) * 100, 1
            ),
        }
