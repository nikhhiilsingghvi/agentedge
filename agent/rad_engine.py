"""
agent/rad_engine.py — Resource-Adaptive Decomposition Engine (Novel Contribution #2)
=====================================================================================

The RADEngine implements the resource-adaptive task decomposition algorithm.
Given a plan step and the current resource state, it decides:

  NORMAL_EXECUTION   — Resources are sufficient; execute the step as planned.
  SIMPLIFIED_PAYLOAD — RAM is in WARN range; strip non-essential payload fields
                       and truncate long messages while preserving command semantics.
  DEFERRED_EXECUTION — RAM is CRITICAL and the step cannot be safely split;
                       record as deferred (do not silently drop or corrupt).
  RESOURCE_FAILURE   — RAM is CRITICAL AND CRITICAL sensor safety lock applies;
                       raise immediately for safety-critical commands.

SubStep objects represent a decomposed fragment of a parent step.
RADMetrics tracks all adaptation decisions for the session.

DESIGN CONSTRAINTS
------------------
- can_split=False steps are NEVER silently changed. If resources are CRITICAL,
  safety-critical steps are still executed at full tier. Only WARN → simplified
  payload is allowed for can_split=True steps.
- Payload simplification is lossless for command semantics: the core command
  and severity fields are always preserved.
- DEFERRED steps are logged with the deferral reason and surfaced in RADMetrics.
- The RADEngine produces no side effects; it only produces decisions.
  Execution is performed by Executor.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any

from core.logger import get_logger
from core.resource_monitor import ResourceState

logger = get_logger("RADEngine")


class RADDecision(Enum):
    """
    Outcome of a RAD resource-tiering decision.

    NORMAL_EXECUTION   : Execute the step as planned, full payload.
    SIMPLIFIED_PAYLOAD : Execute with reduced payload (WARN tier).
    DEFERRED_EXECUTION : Step cannot execute safely under current resources;
                         defer to next cycle. Only for can_split=True steps.
    RESOURCE_FAILURE   : Cannot execute — reported, not silently dropped.
                         Used when CRITICAL resource + step is safety-critical
                         AND execution would be dangerous. Currently unused
                         because safety-critical steps always proceed at NORMAL.
    """
    NORMAL_EXECUTION   = "NORMAL_EXECUTION"
    SIMPLIFIED_PAYLOAD = "SIMPLIFIED_PAYLOAD"
    DEFERRED_EXECUTION = "DEFERRED_EXECUTION"
    RESOURCE_FAILURE   = "RESOURCE_FAILURE"


@dataclass
class SubStep:
    """
    A decomposed fragment of a parent plan step.

    Produced when SIMPLIFIED_PAYLOAD is selected:
    the parent step is reduced to a simplified version.
    Currently only one SubStep is generated per parent step.
    Future work: compound ACTUATOR steps may generate 2–3 SubSteps.
    """
    parent_step_id: str
    substep_id:     str
    target:         str
    command:        str
    payload:        Optional[dict]
    priority:       int          # 1 = highest (safety), 3 = lowest (telemetry)
    notes:          str

    def to_dict(self) -> dict:
        return {
            "parent_step_id": self.parent_step_id,
            "substep_id":     self.substep_id,
            "target":         self.target,
            "command":        self.command,
            "payload":        self.payload,
            "priority":       self.priority,
            "notes":          self.notes,
        }


@dataclass
class RADMetrics:
    """
    Session-level metrics for the RAD algorithm.

    All 7 fields are defined and calculated by RADEngine.record().

    Metrics:
        total_decisions          : Total number of RAD decisions made
        normal_count             : Steps executed at NORMAL tier
        simplified_count         : Steps simplified at WARN tier
        deferred_count           : Steps deferred due to CRITICAL resources
        resource_failure_count   : Steps that could not execute (safety abort)
        simplification_rate_pct  : simplified_count / total_decisions * 100
        deferral_rate_pct        : deferred_count / total_decisions * 100
    """
    total_decisions:         int = 0
    normal_count:            int = 0
    simplified_count:        int = 0
    deferred_count:          int = 0
    resource_failure_count:  int = 0

    @property
    def simplification_rate_pct(self) -> float:
        if self.total_decisions == 0:
            return 0.0
        return round(self.simplified_count / self.total_decisions * 100, 2)

    @property
    def deferral_rate_pct(self) -> float:
        if self.total_decisions == 0:
            return 0.0
        return round(self.deferred_count / self.total_decisions * 100, 2)

    def to_dict(self) -> dict:
        return {
            "total_decisions":         self.total_decisions,
            "normal_count":            self.normal_count,
            "simplified_count":        self.simplified_count,
            "deferred_count":          self.deferred_count,
            "resource_failure_count":  self.resource_failure_count,
            "simplification_rate_pct": self.simplification_rate_pct,
            "deferral_rate_pct":       self.deferral_rate_pct,
        }

    def reset(self) -> None:
        """Reset all metrics counters (e.g., between benchmark tasks)."""
        self.total_decisions        = 0
        self.normal_count           = 0
        self.simplified_count       = 0
        self.deferred_count         = 0
        self.resource_failure_count = 0


# Fields in a payload that are optional/verbose and may be stripped at WARN tier.
# Core semantic fields (message, severity, command) are never dropped.
_DROPPABLE_PAYLOAD_FIELDS = frozenset({
    "timestamp", "details", "stack_trace", "raw_data",
    "metadata", "diagnostics", "trace_id", "session_id",
})

# Message length above which the WARN tier will truncate
_MAX_MESSAGE_LEN = 80


class RADEngine:
    """
    Resource-Adaptive Decomposition Engine.

    Decide: Given a step and current resource state, what tier of execution
    should the step run at?

    Decompose: Given a step in SIMPLIFIED_PAYLOAD tier, produce a SubStep
    list with simplified payload.

    Record: Update RADMetrics counters after a decision is made.
    """

    def __init__(self) -> None:
        self.metrics = RADMetrics()
        logger.info("[rad] RADEngine initialized")

    def decide(
        self,
        step,               # PlanStep
        resource_state: ResourceState,
        ram_percent: float,
    ) -> RADDecision:
        """
        Make a tiering decision for a single plan step.

        Safety contract:
            can_split=False steps ALWAYS return NORMAL_EXECUTION regardless
            of resource state. This covers safety-critical commands (relay:OFF,
            buzzer:ON in fault context) that must never be degraded.

        Args:
            step           : PlanStep dataclass
            resource_state : ResourceState enum from ResourceMonitor
            ram_percent    : Current RAM usage % (for logging)

        Returns:
            RADDecision enum value
        """
        # Safety lock: can_split=False steps always run at NORMAL
        if not step.can_split:
            decision = RADDecision.NORMAL_EXECUTION
            logger.debug(
                "[rad] Step %s: can_split=False → NORMAL (safety lock)",
                step.step_id,
            )

        elif resource_state == ResourceState.NORMAL:
            decision = RADDecision.NORMAL_EXECUTION
            logger.debug("[rad] Step %s: NORMAL resources → NORMAL_EXECUTION", step.step_id)

        elif resource_state == ResourceState.WARN:
            decision = RADDecision.SIMPLIFIED_PAYLOAD
            logger.info(
                "[rad] Step %s: WARN resources (RAM %.1f%%) → SIMPLIFIED_PAYLOAD",
                step.step_id, ram_percent,
            )

        elif resource_state == ResourceState.CRITICAL:
            # CRITICAL + can_split=True → defer this step
            decision = RADDecision.DEFERRED_EXECUTION
            logger.warning(
                "[rad] Step %s: CRITICAL resources (RAM %.1f%%) → DEFERRED_EXECUTION",
                step.step_id, ram_percent,
            )

        else:
            # Unknown state — default to NORMAL for safety
            decision = RADDecision.NORMAL_EXECUTION
            logger.warning("[rad] Step %s: unknown ResourceState %s → NORMAL", step.step_id, resource_state)

        return decision

    def decompose(self, step, decision: RADDecision) -> List[SubStep]:
        """
        Produce SubStep list for a given decision.

        Only SIMPLIFIED_PAYLOAD decisions produce a SubStep.
        NORMAL and DEFERRED produce an empty list (handled by executor directly).

        Args:
            step     : PlanStep
            decision : RADDecision returned by decide()

        Returns:
            List of SubStep objects (usually 0 or 1 elements)
        """
        if decision != RADDecision.SIMPLIFIED_PAYLOAD:
            return []

        simplified_payload = self._simplify_payload(step.payload)

        substep = SubStep(
            parent_step_id=step.step_id,
            substep_id=f"{step.step_id}_SS1",
            target=step.target,
            command=step.command,
            payload=simplified_payload,
            priority=2,  # Medium priority for WARN-tier steps
            notes=f"WARN-tier simplification of {step.step_id}",
        )
        return [substep]

    def record(self, decision: RADDecision) -> None:
        """Update metrics counters after a decision is made."""
        self.metrics.total_decisions += 1
        if decision == RADDecision.NORMAL_EXECUTION:
            self.metrics.normal_count += 1
        elif decision == RADDecision.SIMPLIFIED_PAYLOAD:
            self.metrics.simplified_count += 1
        elif decision == RADDecision.DEFERRED_EXECUTION:
            self.metrics.deferred_count += 1
        elif decision == RADDecision.RESOURCE_FAILURE:
            self.metrics.resource_failure_count += 1

    def reset_metrics(self) -> None:
        """Reset all RAD metrics (e.g., between benchmark tasks)."""
        self.metrics.reset()

    @staticmethod
    def _simplify_payload(payload: Optional[dict]) -> Optional[dict]:
        """
        Reduce payload size for WARN-tier execution.

        Rules:
          - Drop fields in _DROPPABLE_PAYLOAD_FIELDS
          - Truncate 'message' to _MAX_MESSAGE_LEN characters
          - Always preserve 'severity' and 'command' fields
          - Return None if the result would be an empty dict (not worth sending)
        """
        if not payload:
            return payload

        simplified: Dict[str, Any] = {}
        for key, val in payload.items():
            if key in _DROPPABLE_PAYLOAD_FIELDS:
                continue
            if key == "message" and isinstance(val, str) and len(val) > _MAX_MESSAGE_LEN:
                simplified[key] = val[:_MAX_MESSAGE_LEN - 3] + "..."
            else:
                simplified[key] = val

        return simplified if simplified else None

    def get_metrics(self) -> RADMetrics:
        """Return current RADMetrics snapshot."""
        return self.metrics
