"""
AgentEdge-Bench v1.1
====================
20 tasks across 4 categories:
  Category A (Tasks 1-5):   Normal operation monitoring
  Category B (Tasks 6-10):  Single-sensor fault response
  Category C (Tasks 11-15): Multi-sensor fault response
  Category D (Tasks 16-20): Sensor failure + recovery scenarios

Each task is evaluated on:
  - Task completion rate (0/1)
  - Correct action taken (0/1)  -- FIXED: now actually checks executed actions
  - Plan latency (ms)
  - Peak RAM during cycle (%)
  - Energy per cycle (mJ)
  - Recovery success (if applicable)

CHANGES in v1.1:
  - force_readings now deterministically injected before each cycle
  - actions_correct now compares actual executed actions to expected_actions
  - failure_type is now passed to simulate_sensor_failure()
  - reset_state() called between tasks for complete isolation
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Set

from config.settings import LOG_DIR, BENCHMARK_RESULTS_DIR
from core.logger import get_logger
from sensors.simulator import SensorSimulator, SensorStatus
from sensors.simulator import SensorReading

logger = get_logger("Benchmark")


BENCHMARK_TASKS = [
    {
        "task_id": "A01",
        "category": "A",
        "name": "Normal Idle Monitoring",
        "description": "All sensors normal. Agent should output NORMAL plan with no actuator changes.",
        "expected_severity": "NORMAL",
        "expected_actions": ["led_grn:ON"],
        "inject_fault": False,
        "inject_failure": None,
    },
    {
        "task_id": "A02",
        "category": "A",
        "name": "Vibration Spike — Transient",
        "description": "Brief vibration spike within warning range. Agent should log warning, not shutdown.",
        "expected_severity": "WARNING",
        "expected_actions": ["led_yel:ON", "ble:ALERT"],
        "inject_fault": False,
        "inject_failure": None,
        "force_readings": {"mpu6050": 2.1},
    },
    {
        "task_id": "A03",
        "category": "A",
        "name": "Temperature Rise — Early Warning",
        "description": "Temperature in warning zone. Agent should alert, not shutdown.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT"],
        "inject_fault": False,
        "inject_failure": None,
        "force_readings": {"dht22": 52.0},
    },
    {
        "task_id": "A04",
        "category": "A",
        "name": "Gas Level Warning",
        "description": "Gas approaching warning threshold. Alert expected.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT"],
        "inject_fault": False,
        "inject_failure": None,
        "force_readings": {"mq135": 350.0},
    },
    {
        "task_id": "A05",
        "category": "A",
        "name": "Conveyor Near-Jam",
        "description": "Object close to conveyor (12cm). Warning but not yet jam.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT"],
        "inject_fault": False,
        "inject_failure": None,
        "force_readings": {"hcsr04": 12.0},
    },

    {
        "task_id": "B01",
        "category": "B",
        "name": "Critical Vibration Fault",
        "description": "Vibration exceeds 3.0g. Agent MUST shutdown motor and alert.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "buzzer:ON", "led_red:ON", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"mpu6050": 3.8},
    },
    {
        "task_id": "B02",
        "category": "B",
        "name": "Critical Temperature Fault",
        "description": "Temperature exceeds 60°C. Agent must shutdown and alert.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "buzzer:ON", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"dht22": 65.0},
    },
    {
        "task_id": "B03",
        "category": "B",
        "name": "Overcurrent Fault",
        "description": "Current exceeds 2.0A. Motor overcurrent — shutdown required.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"ina219": 2.4},
    },
    {
        "task_id": "B04",
        "category": "B",
        "name": "Conveyor Jam Detected",
        "description": "Object at 6cm — conveyor jam. Agent should alert and stop conveyor.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"hcsr04": 6.0},
    },
    {
        "task_id": "B05",
        "category": "B",
        "name": "Critical Gas Fault",
        "description": "Fume level at 450 ppm — dangerous. Shutdown and ventilation alert.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"mq135": 450.0},
    },

    # ── Category C: Multi-Sensor Fault ────────────────────────────────────────
    {
        "task_id": "C01",
        "category": "C",
        "name": "Vibration + Temperature Combined Fault",
        "description": "Both vibration and temperature critical — compound fault scenario.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "buzzer:ON", "led_red:ON", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"mpu6050": 4.2, "dht22": 68.0},
    },
    {
        "task_id": "C02",
        "category": "C",
        "name": "Current + Temperature + Gas Triple Fault",
        "description": "Three simultaneous faults — motor fire scenario.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "buzzer:ON", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"ina219": 2.8, "dht22": 72.0, "mq135": 520.0},
    },
    {
        "task_id": "C03",
        "category": "C",
        "name": "Vibration + Jam Combined",
        "description": "Bearing fault AND conveyor jam simultaneously.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"mpu6050": 3.5, "hcsr04": 5.0},
    },
    {
        "task_id": "C04",
        "category": "C",
        "name": "Warning on 4 Sensors",
        "description": "4 sensors in warning — no single critical but compound concern.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT", "led_yel:ON"],
        "inject_fault": False,
        "inject_failure": None,
        "force_readings": {"mpu6050": 2.0, "dht22": 50.0, "ina219": 1.7, "mq135": 300.0},
    },
    {
        "task_id": "C05",
        "category": "C",
        "name": "All Sensors Critical",
        "description": "All 5 sensors in fault — catastrophic failure scenario.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "buzzer:ON", "led_red:ON", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"mpu6050": 4.5, "dht22": 75.0, "ina219": 3.0, "hcsr04": 3.0, "mq135": 600.0},
    },

    # ── Category D: Sensor Failure + Recovery ────────────────────────────────
    {
        "task_id": "D01",
        "category": "D",
        "name": "MPU6050 Timeout Failure",
        "description": "Vibration sensor times out. Agent must recover and continue.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT"],
        "inject_fault": False,
        "inject_failure": "mpu6050",
        "failure_type": "TIMEOUT",
    },
    {
        "task_id": "D02",
        "category": "D",
        "name": "DHT22 Bad Data Failure",
        "description": "Temperature sensor returns checksum error. Recovery with substitute value.",
        "expected_severity": "NORMAL",
        "expected_actions": [],
        "inject_fault": False,
        "inject_failure": "dht22",
        "failure_type": "BAD_DATA",
    },
    {
        "task_id": "D03",
        "category": "D",
        "name": "INA219 Bus Error During Fault",
        "description": "Current sensor fails exactly when machine is in fault state.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": "ina219",
        "failure_type": "BUS_ERROR",
        "force_readings": {"mpu6050": 3.8},
    },
    {
        "task_id": "D04",
        "category": "D",
        "name": "Two Sensors Fail Simultaneously",
        "description": "Both HC-SR04 and MQ-135 fail simultaneously.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT"],
        "inject_fault": False,
        "inject_failure": ["hcsr04", "mq135"],
        "failure_type": "TIMEOUT",
    },
    {
        "task_id": "D05",
        "category": "D",
        "name": "Critical Sensor Disconnect (ABORT Protocol)",
        "description": "MPU6050 physically disconnects — ABORT protocol should trigger.",
        "expected_severity": "CRITICAL",
        "expected_actions": ["buzzer:ON", "ble:ALERT"],
        "inject_fault": False,
        "inject_failure": "mpu6050",
        "failure_type": "DISCONNECT",
    },
]

@dataclass
class TaskResult:
    task_id:            str
    category:           str
    task_name:          str
    completed:          bool
    severity_correct:   bool
    actions_correct:    bool
    plan_latency_ms:    float
    total_latency_ms:   float
    peak_ram_percent:   float
    energy_mj:          float
    recovery_triggered: bool
    recovery_success:   Optional[bool]
    notes:              str
    timestamp:          float
    # Grading detail fields (new in v1.1)
    expected_actions:   List[str] = field(default_factory=list)
    executed_actions:   List[str] = field(default_factory=list)
    missing_actions:    List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d

    @property
    def score(self) -> float:
        s = 0.0
        if self.completed:        s += 0.4
        if self.severity_correct:  s += 0.3
        if self.actions_correct:   s += 0.3
        return round(s, 2)


def _check_actions_correct(
    executed_actions: List[str],
    expected_actions: List[str],
) -> tuple[bool, List[str]]:
    """
    Grade whether the agent executed the required actions.

    Args:
        executed_actions: List of "target:command" strings from the cycle's plan steps
        expected_actions: List of "target:command" strings from the task definition

    Returns:
        (actions_correct: bool, missing_actions: List[str])

    Grading rule: Every required action must appear in the executed list.
    Order is not checked (the LLM may reorder safely).
    Extra actions beyond the required set do NOT penalize the score.
    An empty expected_actions list means "any output is acceptable" → True.
    """
    if not expected_actions:
        return True, []

    executed_set: Set[str] = set(a.upper() for a in executed_actions)
    missing = []
    for req in expected_actions:
        if req.upper() not in executed_set:
            missing.append(req)

    return len(missing) == 0, missing


class BenchmarkRunner:
    """
    Runs all 20 tasks sequentially, evaluating each on:
    - Completion, severity correctness, action correctness (REAL grading)
    - Latency, RAM, energy measurements
    - Recovery protocol effectiveness
    Saves complete results to JSON for paper analysis.
    """
    def __init__(self, agent_loop):
        self.agent_loop = agent_loop
        self._results: List[TaskResult] = []
        self.run_id = str(uuid.uuid4())[:6].upper()
        os.makedirs(BENCHMARK_RESULTS_DIR, exist_ok=True)
        logger.info("[bench] AgentEdge-Bench v1.2 initialized | Run: %s", self.run_id)

    def run_all(self) -> Dict[str, Any]:
        logger.info("[bench] 🏁 Starting AgentEdge-Bench | 20 tasks")
        logger.info("[bench] Run ID: %s", self.run_id)

        for i, task_def in enumerate(BENCHMARK_TASKS, 1):
            logger.info("")
            logger.info("[bench] ── Task %d/20: %s — %s", i, task_def["task_id"], task_def["name"])
            result = self._run_task(task_def)
            self._results.append(result)
            logger.info(
                "[bench] Score: %.1f/1.0 | Latency: %.0f ms | RAM: %.1f%% | Actions: %s",
                result.score, result.total_latency_ms, result.peak_ram_percent,
                "✅" if result.actions_correct else f"❌ missing={result.missing_actions}",
            )

        return self._generate_report()

    def _run_task(self, task_def: dict) -> TaskResult:
        t0 = time.time()
        sensor_sim = self.agent_loop.sensor_sim
        actuator_sim = self.agent_loop.actuator_sim
        recovery = self.agent_loop.recovery

        # ── Phase 1 Fix: Full state reset for task isolation ──────────────────
        sensor_sim.reset_state()
        actuator_sim.reset_state()
        recovery.reset_cycle_history()

        # ── Phase 1 Fix: Apply forced readings BEFORE the cycle runs ─────────
        force_readings = task_def.get("force_readings", {})
        for sensor_id, value in force_readings.items():
            sensor_sim.force_reading(sensor_id, value)
            logger.debug("[bench] Forced reading: %s = %.4f", sensor_id, value)

        # ── Phase 1 Fix: Inject failure with correct type ─────────────────────
        failures_injected = []
        if task_def.get("inject_failure"):
            fail_targets = task_def["inject_failure"]
            if isinstance(fail_targets, str):
                fail_targets = [fail_targets]
            # Use the task's specified failure_type, default to TIMEOUT
            failure_type = task_def.get("failure_type", "TIMEOUT")
            for sid in fail_targets:
                sensor_sim.simulate_sensor_failure(sid, failure_type=failure_type)
                failures_injected.append(sid)

        # Bug 3d: snapshot recovery count BEFORE task to enable task-local slicing
        history_before = recovery.get_statistics().get("total_failures", 0)

        try:
            cycle = self.agent_loop.run_single_cycle()

            # ── Severity grading ──────────────────────────────────────────────
            # Bug 3f: exact equality only (substring 'NORMAL' in 'ABNORMAL' = True)
            severity_correct = (cycle.plan_severity == task_def["expected_severity"])

            # ── Phase 1 Fix: Real action grading ─────────────────────────────
            # Collect executed actions from the cycle's last plan steps
            executed_actions = _extract_executed_actions(self.agent_loop)
            expected_actions = task_def.get("expected_actions", [])
            actions_correct, missing_actions = _check_actions_correct(
                executed_actions, expected_actions
            )

            # Bug 3d: task-local recovery events only (not cumulative session stats)
            history_after = recovery.get_statistics().get("total_failures", 0)
            task_local_failures = history_after - history_before
            recovery_triggered = task_local_failures > 0
            if recovery_triggered and hasattr(recovery, "_recovery_history"):
                task_history = recovery._recovery_history[-task_local_failures:]
                task_successes = sum(
                    1 for r in task_history
                    if r.get("outcome") in ("SUCCESS", "PARTIAL")
                )
                recovery_success = task_successes > 0
            elif recovery_triggered:
                recovery_success = (
                    recovery.get_statistics().get("success_rate_pct", 100) > 50
                )
            else:
                recovery_success = None

            # Bug 3c: completed requires severity correct AND execution succeeded
            task_completed = severity_correct and cycle.execution_success

            result = TaskResult(
                task_id=task_def["task_id"],
                category=task_def["category"],
                task_name=task_def["name"],
                completed=task_completed,       # Bug 3c fix
                severity_correct=severity_correct,
                actions_correct=actions_correct,
                plan_latency_ms=cycle.phase_latencies.get("plan_ms", 0),
                total_latency_ms=cycle.total_latency_ms,
                peak_ram_percent=cycle.peak_ram_percent,
                energy_mj=cycle.total_energy_mj,
                recovery_triggered=recovery_triggered,
                recovery_success=recovery_success,
                notes="OK" if (task_completed and actions_correct) else (
                    f"Sev: {'OK' if severity_correct else 'FAIL'} | "
                    f"Exec: {'OK' if cycle.execution_success else 'FAIL'} | "
                    f"Missing: {missing_actions}"
                ),
                timestamp=t0,
                expected_actions=expected_actions,
                executed_actions=executed_actions,
                missing_actions=missing_actions,
            )

        except Exception as e:
            logger.error("[bench] Task %s failed with exception: %s", task_def["task_id"], e)
            result = TaskResult(
                task_id=task_def["task_id"],
                category=task_def["category"],
                task_name=task_def["name"],
                completed=False,
                severity_correct=False,
                actions_correct=False,
                plan_latency_ms=0,
                total_latency_ms=(time.time() - t0) * 1000,
                peak_ram_percent=0,
                energy_mj=0,
                recovery_triggered=False,
                recovery_success=None,
                notes=f"Exception: {e}",
                timestamp=t0,
                expected_actions=task_def.get("expected_actions", []),
                executed_actions=[],
                missing_actions=task_def.get("expected_actions", []),
            )

        finally:
            # Restore sensors and clear forced readings after every task
            for sid in failures_injected:
                sensor_sim.restore_sensor(sid)
            sensor_sim.clear_forced_readings()

        return result

    def _generate_report(self) -> Dict[str, Any]:
        total = len(self._results)
        completed = sum(1 for r in self._results if r.completed)
        sev_correct = sum(1 for r in self._results if r.severity_correct)
        act_correct = sum(1 for r in self._results if r.actions_correct)
        avg_score = sum(r.score for r in self._results) / total if total > 0 else 0

        latencies = [r.plan_latency_ms for r in self._results if r.plan_latency_ms > 0]
        rams = [r.peak_ram_percent for r in self._results if r.peak_ram_percent > 0]
        energies = [r.energy_mj for r in self._results if r.energy_mj > 0]

        by_category = {}
        for r in self._results:
            cat = r.category
            if cat not in by_category:
                by_category[cat] = {"tasks": 0, "completed": 0, "avg_score": 0}
            by_category[cat]["tasks"] += 1
            by_category[cat]["completed"] += int(r.completed)
            by_category[cat]["avg_score"] = round(
                sum(rr.score for rr in self._results if rr.category == cat) /
                by_category[cat]["tasks"], 2
            )

        report = {
            "benchmark":              "AgentEdge-Bench v1.1",
            "run_id":                 self.run_id,
            "total_tasks":            total,
            "completed_tasks":        completed,
            "completion_rate_pct":    round(completed / total * 100, 1),
            "severity_accuracy_pct":  round(sev_correct / total * 100, 1),
            "action_accuracy_pct":    round(act_correct / total * 100, 1),
            "avg_composite_score":    round(avg_score, 3),
            "avg_plan_latency_ms":    round(sum(latencies) / len(latencies), 1) if latencies else 0,
            "avg_peak_ram_pct":       round(sum(rams) / len(rams), 1) if rams else 0,
            "avg_energy_mj":          round(sum(energies) / len(energies), 2) if energies else 0,
            "by_category":            by_category,
            "task_results":           [r.to_dict() for r in self._results],
        }

        report_path = os.path.join(
            BENCHMARK_RESULTS_DIR,
            f"bench_{self.run_id}_{int(time.time())}.json"
        )
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

        logger.info("[bench] 📊 Benchmark complete!")
        logger.info(
            "[bench] Completion: %.1f%% | Severity: %.1f%% | Action Accuracy: %.1f%% | Score: %.3f",
            report["completion_rate_pct"],
            report["severity_accuracy_pct"],
            report["action_accuracy_pct"],
            report["avg_composite_score"],
        )
        logger.info("[bench] Avg Latency: %.0f ms | Avg RAM: %.1f%% | Avg Energy: %.1f mJ",
                    report["avg_plan_latency_ms"],
                    report["avg_peak_ram_pct"],
                    report["avg_energy_mj"])
        logger.info("[bench] Report saved: %s", report_path)

        return report


def _extract_executed_actions(agent_loop) -> List[str]:
    """
    Extract the list of "target:command" strings from the most recent cycle's
    plan execution. Reads from the agent loop's last execution result.

    Returns e.g. ["relay:OFF", "buzzer:ON", "ble:ALERT"]
    """
    try:
        last_plan = getattr(agent_loop, "_last_plan", None)
        if last_plan is None:
            return []
        actions = []
        for step in last_plan.steps:
            if step.action_type in ("ACTUATOR", "ALERT"):
                target = step.target.lower()
                command = step.command.upper()
                actions.append(f"{target}:{command}")
        return actions
    except Exception:
        return []


import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

from config.settings import LOG_DIR, BENCHMARK_RESULTS_DIR
from core.logger import get_logger
from sensors.simulator import SensorSimulator, SensorStatus
from sensors.simulator import SensorReading

logger = get_logger("Benchmark")


BENCHMARK_TASKS = [
    {
        "task_id": "A01",
        "category": "A",
        "name": "Normal Idle Monitoring",
        "description": "All sensors normal. Agent should output NORMAL plan with no actuator changes.",
        "expected_severity": "NORMAL",
        "expected_actions": ["led_grn:ON"],
        "inject_fault": False,
        "inject_failure": None,
    },
    {
        "task_id": "A02",
        "category": "A",
        "name": "Vibration Spike — Transient",
        "description": "Brief vibration spike within warning range. Agent should log warning, not shutdown.",
        "expected_severity": "WARNING",
        "expected_actions": ["led_yel:ON", "ble:ALERT"],
        "inject_fault": False,
        "inject_failure": None,
        "force_readings": {"mpu6050": 2.1},
    },
    {
        "task_id": "A03",
        "category": "A",
        "name": "Temperature Rise — Early Warning",
        "description": "Temperature in warning zone. Agent should alert, not shutdown.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT"],
        "inject_fault": False,
        "inject_failure": None,
        "force_readings": {"dht22": 52.0},
    },
    {
        "task_id": "A04",
        "category": "A",
        "name": "Gas Level Warning",
        "description": "Gas approaching warning threshold. Alert expected.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT"],
        "inject_fault": False,
        "inject_failure": None,
        "force_readings": {"mq135": 350.0},
    },
    {
        "task_id": "A05",
        "category": "A",
        "name": "Conveyor Near-Jam",
        "description": "Object close to conveyor (12cm). Warning but not yet jam.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT"],
        "inject_fault": False,
        "inject_failure": None,
        "force_readings": {"hcsr04": 12.0},
    },

    {
        "task_id": "B01",
        "category": "B",
        "name": "Critical Vibration Fault",
        "description": "Vibration exceeds 3.0g. Agent MUST shutdown motor and alert.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "buzzer:ON", "led_red:ON", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"mpu6050": 3.8},
    },
    {
        "task_id": "B02",
        "category": "B",
        "name": "Critical Temperature Fault",
        "description": "Temperature exceeds 60°C. Agent must shutdown and alert.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "buzzer:ON", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"dht22": 65.0},
    },
    {
        "task_id": "B03",
        "category": "B",
        "name": "Overcurrent Fault",
        "description": "Current exceeds 2.0A. Motor overcurrent — shutdown required.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"ina219": 2.4},
    },
    {
        "task_id": "B04",
        "category": "B",
        "name": "Conveyor Jam Detected",
        "description": "Object at 6cm — conveyor jam. Agent should alert and stop conveyor.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"hcsr04": 6.0},
    },
    {
        "task_id": "B05",
        "category": "B",
        "name": "Critical Gas Fault",
        "description": "Fume level at 450 ppm — dangerous. Shutdown and ventilation alert.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"mq135": 450.0},
    },

    # ── Category C: Multi-Sensor Fault ────────────────────────────────────────
    {
        "task_id": "C01",
        "category": "C",
        "name": "Vibration + Temperature Combined Fault",
        "description": "Both vibration and temperature critical — compound fault scenario.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "buzzer:ON", "led_red:ON", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"mpu6050": 4.2, "dht22": 68.0},
    },
    {
        "task_id": "C02",
        "category": "C",
        "name": "Current + Temperature + Gas Triple Fault",
        "description": "Three simultaneous faults — motor fire scenario.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "buzzer:ON", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"ina219": 2.8, "dht22": 72.0, "mq135": 520.0},
    },
    {
        "task_id": "C03",
        "category": "C",
        "name": "Vibration + Jam Combined",
        "description": "Bearing fault AND conveyor jam simultaneously.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"mpu6050": 3.5, "hcsr04": 5.0},
    },
    {
        "task_id": "C04",
        "category": "C",
        "name": "Warning on 4 Sensors",
        "description": "4 sensors in warning — no single critical but compound concern.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT", "led_yel:ON"],
        "inject_fault": False,
        "inject_failure": None,
        "force_readings": {"mpu6050": 2.0, "dht22": 50.0, "ina219": 1.7, "mq135": 300.0},
    },
    {
        "task_id": "C05",
        "category": "C",
        "name": "All Sensors Critical",
        "description": "All 5 sensors in fault — catastrophic failure scenario.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "buzzer:ON", "led_red:ON", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": None,
        "force_readings": {"mpu6050": 4.5, "dht22": 75.0, "ina219": 3.0, "hcsr04": 3.0, "mq135": 600.0},
    },

    # ── Category D: Sensor Failure + Recovery ────────────────────────────────
    {
        "task_id": "D01",
        "category": "D",
        "name": "MPU6050 Timeout Failure",
        "description": "Vibration sensor times out. Agent must recover and continue.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT"],
        "inject_fault": False,
        "inject_failure": "mpu6050",
    },
    {
        "task_id": "D02",
        "category": "D",
        "name": "DHT22 Bad Data Failure",
        "description": "Temperature sensor returns checksum error. Recovery with substitute value.",
        "expected_severity": "NORMAL",
        "expected_actions": [],
        "inject_fault": False,
        "inject_failure": "dht22",
    },
    {
        "task_id": "D03",
        "category": "D",
        "name": "INA219 Bus Error During Fault",
        "description": "Current sensor fails exactly when machine is in fault state.",
        "expected_severity": "FAULT",
        "expected_actions": ["relay:OFF", "ble:ALERT"],
        "inject_fault": True,
        "inject_failure": "ina219",
        "force_readings": {"mpu6050": 3.8},
    },
    {
        "task_id": "D04",
        "category": "D",
        "name": "Two Sensors Fail Simultaneously",
        "description": "Both HC-SR04 and MQ-135 fail simultaneously.",
        "expected_severity": "WARNING",
        "expected_actions": ["ble:ALERT"],
        "inject_fault": False,
        "inject_failure": ["hcsr04", "mq135"],
    },
    {
        "task_id": "D05",
        "category": "D",
        "name": "Critical Sensor Disconnect (ABORT Protocol)",
        "description": "MPU6050 physically disconnects — ABORT protocol should trigger.",
        "expected_severity": "CRITICAL",
        "expected_actions": ["buzzer:ON", "ble:ALERT"],
        "inject_fault": False,
        "inject_failure": "mpu6050",
        "failure_type": "DISCONNECT",
    },
]

@dataclass
class TaskResult:
    task_id:            str
    category:           str
    task_name:          str
    completed:          bool
    severity_correct:   bool
    actions_correct:    bool
    plan_latency_ms:    float
    total_latency_ms:   float
    peak_ram_percent:   float
    energy_mj:          float
    recovery_triggered: bool
    recovery_success:   Optional[bool]
    notes:              str
    timestamp:          float

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @property
    def score(self) -> float:
        s = 0.0
        if self.completed:       s += 0.4
        if self.severity_correct: s += 0.3
        if self.actions_correct:  s += 0.3
        return round(s, 2)

class BenchmarkRunner:
    """
    Runs all 20 tasks sequentially, evaluating each on:
    - Completion, severity correctness, action correctness
    - Latency, RAM, energy measurements
    - Recovery protocol effectiveness
    Saves complete results to JSON for paper analysis.
    """
    def __init__(self, agent_loop):
        self.agent_loop = agent_loop
        self._results: List[TaskResult] = []
        self.run_id = str(uuid.uuid4())[:6].upper()
        os.makedirs(BENCHMARK_RESULTS_DIR, exist_ok=True)
        logger.info("[bench] AgentEdge-Bench initialized | Run: %s", self.run_id)

    def run_all(self) -> Dict[str, Any]:
        logger.info("[bench] 🏁 Starting AgentEdge-Bench | 20 tasks")
        logger.info("[bench] Run ID: %s", self.run_id)

        for i, task_def in enumerate(BENCHMARK_TASKS, 1):
            logger.info("")
            logger.info("[bench] ── Task %d/20: %s — %s", i, task_def["task_id"], task_def["name"])
            result = self._run_task(task_def)
            self._results.append(result)
            logger.info("[bench] Score: %.1f/1.0 | Latency: %.0f ms | RAM: %.1f%%",
                        result.score, result.total_latency_ms, result.peak_ram_percent)

        return self._generate_report()

    def _run_task(self, task_def: dict) -> TaskResult:
        t0 = time.time()
        sensor_sim = self.agent_loop.sensor_sim
        recovery = self.agent_loop.recovery

        failures_injected = []
        if task_def.get("inject_failure"):
            fail_targets = task_def["inject_failure"]
            if isinstance(fail_targets, str):
                fail_targets = [fail_targets]
            for sid in fail_targets:
                sensor_sim.simulate_sensor_failure(sid)
                failures_injected.append(sid)

        force_readings = task_def.get("force_readings", {})

        try:
            cycle = self.agent_loop.run_single_cycle()
            severity_correct = (
                cycle.plan_severity == task_def["expected_severity"]
                or task_def["expected_severity"] in cycle.plan_severity
            )

            actions_correct = True 
            recovery_triggered = cycle.failures_detected > 0
            recovery_success = (
                recovery.get_statistics().get("success_rate_pct", 100) > 50
                if recovery_triggered else None
            )

            result = TaskResult(
                task_id=task_def["task_id"],
                category=task_def["category"],
                task_name=task_def["name"],
                completed=task_completed,    # Bug 3c: real success criteria
                severity_correct=severity_correct,
                actions_correct=actions_correct,
                plan_latency_ms=cycle.phase_latencies.get("plan_ms", 0),
                total_latency_ms=cycle.total_latency_ms,
                peak_ram_percent=cycle.peak_ram_percent,
                energy_mj=cycle.total_energy_mj,
                recovery_triggered=recovery_triggered,
                recovery_success=recovery_success,
                notes="OK",
                timestamp=t0,
            )

        except Exception as e:
            logger.error("[bench] Task %s failed with exception: %s", task_def["task_id"], e)
            result = TaskResult(
                task_id=task_def["task_id"],
                category=task_def["category"],
                task_name=task_def["name"],
                completed=False,
                severity_correct=False,
                actions_correct=False,
                plan_latency_ms=0,
                total_latency_ms=(time.time() - t0) * 1000,
                peak_ram_percent=0,
                energy_mj=0,
                recovery_triggered=False,
                recovery_success=None,
                notes=f"Exception: {e}",
                timestamp=t0,
            )

        finally:
            for sid in failures_injected:
                sensor_sim.restore_sensor(sid)

        return result

    def _generate_report(self) -> Dict[str, Any]:
        total = len(self._results)
        completed = sum(1 for r in self._results if r.completed)
        sev_correct = sum(1 for r in self._results if r.severity_correct)
        avg_score = sum(r.score for r in self._results) / total if total > 0 else 0

        latencies = [r.plan_latency_ms for r in self._results if r.plan_latency_ms > 0]
        rams = [r.peak_ram_percent for r in self._results if r.peak_ram_percent > 0]
        energies = [r.energy_mj for r in self._results if r.energy_mj > 0]

        by_category = {}
        for r in self._results:
            cat = r.category
            if cat not in by_category:
                by_category[cat] = {"tasks": 0, "completed": 0, "avg_score": 0}
            by_category[cat]["tasks"] += 1
            by_category[cat]["completed"] += int(r.completed)
            by_category[cat]["avg_score"] = round(
                sum(rr.score for rr in self._results if rr.category == cat) /
                by_category[cat]["tasks"], 2
            )

        report = {
            "benchmark": "AgentEdge-Bench v1.0",
            "run_id": self.run_id,
            "total_tasks": total,
            "completed_tasks": completed,
            "completion_rate_pct": round(completed / total * 100, 1),
            "severity_accuracy_pct": round(sev_correct / total * 100, 1),
            "avg_composite_score": round(avg_score, 3),
            "avg_plan_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
            "avg_peak_ram_pct": round(sum(rams) / len(rams), 1) if rams else 0,
            "avg_energy_mj": round(sum(energies) / len(energies), 2) if energies else 0,
            "by_category": by_category,
            "task_results": [r.to_dict() for r in self._results],
        }

        report_path = os.path.join(
            BENCHMARK_RESULTS_DIR,
            f"bench_{self.run_id}_{int(time.time())}.json"
        )
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

        logger.info("[bench] 📊 Benchmark complete!")
        logger.info("[bench] Completion: %.1f%% | Severity Accuracy: %.1f%% | Avg Score: %.3f",
                    report["completion_rate_pct"],
                    report["severity_accuracy_pct"],
                    report["avg_composite_score"])
        logger.info("[bench] Avg Latency: %.0f ms | Avg RAM: %.1f%% | Avg Energy: %.1f mJ",
                    report["avg_plan_latency_ms"],
                    report["avg_peak_ram_pct"],
                    report["avg_energy_mj"])
        logger.info("[bench] Report saved: %s", report_path)

        return report
