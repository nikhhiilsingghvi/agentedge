import signal
import time
import json
import os
import uuid
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from config.settings import (
    AGENT_LOOP_INTERVAL, SIMULATION_MODE, AGENT_MODE, LOG_DIR,
)
from core.logger import get_logger, AgentEventLogger
from core.resource_monitor import ResourceMonitor, ResourceState
from sensors.simulator import SensorSimulator, SensorReading, SensorStatus
from actuators.simulator import ActuatorSimulator
from agent.planner import LLMPlanner, AgentPlan, PlanStep
from agent.executor import Executor, ExecutionResult
from agent.recovery import RecoveryProtocol
from agent.verifier import Verifier, VerificationReport

# Phase 2: Hardcoded safe-state plan used when ABORT is required
# This bypasses the LLM entirely — guarantees relay OFF and alarm ON
_SAFE_STATE_STEPS = [
    PlanStep("SAFE_S1", "Emergency motor shutdown",  "ACTUATOR", "relay",   "OFF",   can_split=False),
    PlanStep("SAFE_S2", "Sound audible alarm",        "ACTUATOR", "buzzer",  "ON",    can_split=False),
    PlanStep("SAFE_S3", "Activate critical fault LED","ACTUATOR", "led_red", "ON",    can_split=False),
    PlanStep("SAFE_S4", "BLE alert to operator",      "ALERT",    "ble",     "ALERT",
             payload={"message": "CRITICAL SENSOR ABORT — manual inspection required",
                      "severity": "CRITICAL"}, can_split=False),
] 

logger = get_logger("AgentLoop", LOG_DIR)

class LoopPhase(Enum):
    IDLE     = "IDLE"
    PERCEIVE = "PERCEIVE"
    PLAN     = "PLAN"
    EXECUTE  = "EXECUTE"
    VERIFY   = "VERIFY"
    RECOVER  = "RECOVER"

@dataclass
class CycleRecord:
    cycle_id:             str
    cycle_number:         int
    phase_latencies:      Dict[str, float]  # phase → ms
    total_latency_ms:     float
    sensor_statuses:      Dict[str, str]
    plan_severity:        str
    plan_steps:           int
    execution_success:    bool
    failures_detected:    int
    recoveries_attempted: int
    peak_ram_percent:     float
    avg_power_w:          float
    total_energy_mj:      float
    timestamp:            float
    # Phase 2: Closed-Loop Verification fields
    verification_success:  bool  = True
    verification_failures: list  = None

    def __post_init__(self):
        if self.verification_failures is None:
            self.verification_failures = []

    def to_dict(self) -> dict:
        return self.__dict__.copy()

class AgentLoop:
    def __init__(self):
        self.session_id = str(uuid.uuid4())[:8].upper()
        self._cycle_count = 0
        self._cycle_records: list = []
        self._current_phase = LoopPhase.IDLE
        self._stop_requested = False

        logger.info("=" * 60)
        logger.info("  AgentEdge v1.1.0 — Session %s", self.session_id)
        logger.info("  Mode: %s", AGENT_MODE.upper())
        logger.info("=" * 60)

        if AGENT_MODE == "hybrid":
            # Hybrid: real MPU6050 + real GPIO; other sensors simulated
            from sensors.hardware_reader import HybridSensorReader
            from actuators.hardware_writer import HardwareActuatorWriter
            self.sensor_sim   = HybridSensorReader()
            self.actuator_sim = HardwareActuatorWriter(sensor_simulator=self.sensor_sim)
            logger.info("[agent] HYBRID mode — MPU6050: [HW] | DHT22/INA219/HC-SR04/MQ-135: [SIM]")
            logger.info("[agent] GPIO actuators: led_red/led_grn/led_yel/buzzer: [HW] | relay: [N/A]")
        else:
            # Simulation (default) — safe to run anywhere
            self.sensor_sim   = SensorSimulator()
            self.actuator_sim = ActuatorSimulator(sensor_simulator=self.sensor_sim)
            logger.info("[agent] SIMULATION mode — all sensors and actuators are software-only")

        self.resource_mon  = ResourceMonitor(simulation_mode=SIMULATION_MODE)
        self.recovery      = RecoveryProtocol()
        self.planner       = LLMPlanner()
        self.executor      = Executor(self.actuator_sim, self.resource_mon)
        self.verifier      = Verifier()   # Phase 2: Closed-Loop State Verification
        self.event_logger  = AgentEventLogger(self.session_id, LOG_DIR)
        self._last_plan: Optional[AgentPlan] = None   # Phase 1/Bench: track last plan for action grading

        os.makedirs(LOG_DIR, exist_ok=True)
        logger.info("[agent] All components initialized — ready to run")

        # Graceful shutdown on SIGINT / SIGTERM
        signal.signal(signal.SIGINT,  self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame) -> None:  # noqa: ANN001
        logger.info("[agent] Signal %d received — requesting graceful shutdown", signum)
        self._stop_requested = True

    def run(self, max_cycles: Optional[int] = None) -> None:
        logger.info("[agent] 🚀 Starting agent loop | max_cycles=%s", max_cycles)
        try:
            while not self._stop_requested:
                if max_cycles and self._cycle_count >= max_cycles:
                    logger.info("[agent] Reached max_cycles=%d — stopping", max_cycles)
                    break

                cycle_record = self._run_cycle()
                self._cycle_records.append(cycle_record.to_dict())

                if self._stop_requested:
                    break
                if max_cycles is None or self._cycle_count < max_cycles:
                    logger.debug("[agent] Sleeping %.1fs until next cycle", AGENT_LOOP_INTERVAL)
                    time.sleep(AGENT_LOOP_INTERVAL)

        except KeyboardInterrupt:
            logger.info("[agent] 🛑 Agent loop stopped by user")
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        """Release hardware resources and save session report."""
        if AGENT_MODE == "hybrid":
            try:
                self.sensor_sim.cleanup()
            except AttributeError:
                pass
            try:
                self.actuator_sim.cleanup()
            except AttributeError:
                pass
            logger.info("[agent] Hardware resources released")
        self._save_session_report()

    def run_single_cycle(self) -> CycleRecord:
        return self._run_cycle()

    def _run_cycle(self) -> CycleRecord:
        self._cycle_count += 1
        cycle_id = f"C{self._cycle_count:04d}_{self.session_id}"
        cycle_start = time.time()
        phase_latencies = {}

        logger.info("")
        logger.info("─" * 50)
        logger.info("  CYCLE %04d  |  Session %s", self._cycle_count, self.session_id)
        logger.info("─" * 50)

        # PHASE 1: PERCEIVE
        self._current_phase = LoopPhase.PERCEIVE
        p1_start = time.time()
        sensor_readings = self._phase_perceive()
        phase_latencies["perceive_ms"] = round((time.time() - p1_start) * 1000, 2)

        # Count failures
        failures = {sid: r for sid, r in sensor_readings.items() if r.is_failed}
        logger.info("[agent] PERCEIVE complete | %d sensors | %d failures | %.0f ms",
                    len(sensor_readings), len(failures), phase_latencies["perceive_ms"])

        # PHASE 2: RECOVER (pre-plan)
        self._current_phase = LoopPhase.RECOVER
        p_rec_start = time.time()
        abort_required = False
        if failures:
            sensor_readings, abort_required = self._phase_recover(sensor_readings, failures, cycle_id)
        phase_latencies["recover_ms"] = round((time.time() - p_rec_start) * 1000, 2)

        # PHASE 3: PLAN
        self._current_phase = LoopPhase.PLAN
        p2_start = time.time()
        self.resource_mon.set_inference_active(True)
        if abort_required:
            # Phase 2: Skip LLM, use hardcoded safe-state plan
            plan = self._make_safe_state_plan(cycle_id)
            logger.error("[agent] 🚨 ABORT path — using hardcoded safe-state plan")
        else:
            plan = self._phase_plan(sensor_readings, cycle_id)
        self.resource_mon.set_inference_active(False)
        self._last_plan = plan   # Store for benchmark action extraction
        phase_latencies["plan_ms"] = round((time.time() - p2_start) * 1000, 2)

        logger.info("[agent] PLAN complete | Severity: %s | Steps: %d | %.0f ms",
                    plan.severity, len(plan.steps), phase_latencies["plan_ms"])

        # PHASE 4: EXECUTE
        self._current_phase = LoopPhase.EXECUTE
        p3_start = time.time()
        exec_result = self._phase_execute(plan)
        phase_latencies["execute_ms"] = round((time.time() - p3_start) * 1000, 2)

        logger.info("[agent] EXECUTE complete | Success: %s | %.0f ms",
                    exec_result.success, phase_latencies["execute_ms"])

        # PHASE 5: VERIFY
        self._current_phase = LoopPhase.VERIFY
        p4_start = time.time()
        verify_report: VerificationReport = self._phase_verify(plan, exec_result)
        phase_latencies["verify_ms"] = round((time.time() - p4_start) * 1000, 2)

        logger.info("[agent] VERIFY complete | success=%s | checked=%d | failed=%d | %.0f ms [SIMULATION]",
                    verify_report.overall_success, verify_report.total_steps_checked,
                    verify_report.failed, phase_latencies["verify_ms"])

        #METRICS
        total_latency_ms = (time.time() - cycle_start) * 1000
        res_stats = self.resource_mon.get_statistics()

        cycle_record = CycleRecord(
            cycle_id=cycle_id,
            cycle_number=self._cycle_count,
            phase_latencies=phase_latencies,
            total_latency_ms=round(total_latency_ms, 2),
            sensor_statuses={sid: r.status.value for sid, r in sensor_readings.items()},
            plan_severity=plan.severity,
            plan_steps=len(plan.steps),
            execution_success=exec_result.success,
            failures_detected=len(failures),
            recoveries_attempted=len(failures),
            peak_ram_percent=res_stats.get("ram_peak_percent", 0.0),
            avg_power_w=res_stats.get("estimated_avg_power_w", 0.0),
            total_energy_mj=round(total_latency_ms / 1000 * res_stats.get("estimated_avg_power_w", 5.0) * 1000, 2),
            timestamp=cycle_start,
            # Phase 2: Closed-Loop Verification
            verification_success=verify_report.overall_success,
            verification_failures=verify_report.verification_failures,
        )

        self.event_logger.log_event(
            "CYCLE_COMPLETE",
            data=cycle_record.to_dict(),
            latency_ms=total_latency_ms,
            ram_percent=res_stats.get("ram_peak_percent"),
            energy_mj=cycle_record.total_energy_mj,
        )

        logger.info(
            "[agent] ⏱️  Cycle %04d complete | Total: %.0f ms | "
            "RAM peak: %.1f%% | Energy: %.1f mJ | Machine: %s",
            self._cycle_count, total_latency_ms,
            cycle_record.peak_ram_percent,
            cycle_record.total_energy_mj,
            self.sensor_sim.machine_state,
        )

        self._current_phase = LoopPhase.IDLE
        return cycle_record

    def _phase_perceive(self) -> Dict[str, SensorReading]:
        readings = self.sensor_sim.read_all()
        for sid, reading in readings.items():
            if not reading.is_failed and reading.value is not None:
                self.recovery.update_last_known_good(sid, reading.value)
        return readings

    def _phase_recover(
        self,
        readings: Dict[str, SensorReading],
        failures: dict,
        cycle_id: str,
    ) -> tuple:
        """
        Returns: (updated_readings, abort_required: bool)
        Phase 2: propagates abort_required back to the cycle loop.
        """
        abort_required = False

        for sid, failed_reading in failures.items():
            logger.warning("[agent] 🔴 Sensor failure: %s — initiating recovery", sid)

            record = self.recovery.recover(
                sensor_id=sid,
                failure_reason=failed_reading.failure_reason,
                read_fn=lambda s=sid: getattr(self.sensor_sim, f"read_{self._sensor_method(s)}")(),
                task_id=cycle_id,
                step_id="PERCEIVE",
            )

            if self.recovery.abort_required:
                abort_required = True

            if record.recovered_value is not None:
                readings[sid].value = record.recovered_value
                # Phase 2: Tag as SUBSTITUTE so LLM knows it is stale data
                if record.substitute_age_s is not None:
                    readings[sid].status = SensorStatus.SUBSTITUTE
                    readings[sid].is_substitute = True
                    readings[sid].substitute_age_s = record.substitute_age_s
                    readings[sid].failure_reason = f"[SUBSTITUTE age={record.substitute_age_s:.1f}s] {record.notes}"
                else:
                    readings[sid].status = SensorStatus.OK
                    readings[sid].failure_reason = f"[RECOVERED] {record.notes}"
                logger.info("[agent] ✅ Sensor %s recovered: %.3f", sid, record.recovered_value)
            else:
                logger.warning("[agent] ⚡ Sensor %s not recovered — proceeding without it", sid)

        return readings, abort_required

    def _phase_plan(self, readings: Dict[str, SensorReading], cycle_id: str) -> AgentPlan:
        context = f"Cycle: {cycle_id} | Machine state: {self.sensor_sim.machine_state}"
        return self.planner.plan(readings, context=context)

    def _make_safe_state_plan(self, cycle_id: str) -> AgentPlan:
        """Phase 2: Hardcoded emergency plan used when ABORT is required."""
        from agent.planner import AgentPlan
        return AgentPlan(
            plan_id=f"SAFE_{cycle_id}",
            situation="Critical sensor abort — safe state activated",
            severity="CRITICAL",
            steps=list(_SAFE_STATE_STEPS),
            reasoning="ABORT protocol: critical sensor disconnected, bypassing LLM for safety.",
            latency_ms=0.0,
            model_used="SAFE_STATE_HARDCODED",
            token_count=0,
        )

    def _phase_execute(self, plan: AgentPlan) -> ExecutionResult:
        return self.executor.execute_plan(plan)

    def _phase_verify(self, plan: AgentPlan, exec_result: ExecutionResult) -> VerificationReport:
        """
        Phase 5: Closed-Loop State Verification (Novel Contribution #3).

        Compares the expected actuator state for each plan step against
        the observed actuator state in the simulator.

        NOTE: This verifies the software model only (ActuatorSimulator).
        Physical hardware verification (GPIO, relay continuity) is out of scope
        for the simulation mode used in AgentEdge-Bench.
        """
        return self.verifier.verify_plan(plan, exec_result, self.actuator_sim)

    @staticmethod
    def _sensor_method(sensor_id: str) -> str:
        return {
            "mpu6050": "vibration",
            "dht22":   "temperature",
            "ina219":  "current",
            "hcsr04":  "distance",
            "mq135":   "gas",
        }.get(sensor_id, sensor_id)

    def _save_session_report(self) -> None:
        report = {
            "session_id":     self.session_id,
            "total_cycles":   self._cycle_count,
            "planner_stats":  self.planner.get_statistics(),
            "executor_stats": self.executor.get_statistics(),
            "resource_stats": self.resource_mon.get_statistics(),
            "recovery_stats": self.recovery.get_statistics(),
            "rad_metrics":    self.executor.get_rad_metrics(),   # Phase 3
            "event_summary":  self.event_logger.get_session_summary(),
            "cycle_records":  self._cycle_records,
        }
        os.makedirs(LOG_DIR, exist_ok=True)
        report_path = os.path.join(LOG_DIR, f"session_{self.session_id}_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

        logger.info("[agent] 📊 Session report saved: %s", report_path)
        self._print_summary(report)

    def _print_summary(self, report: dict) -> None:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel

        console = Console()
        console.print("\n")
        console.print(Panel.fit(
            f"[bold cyan]AgentEdge Session Report[/bold cyan]\n"
            f"Session ID: [yellow]{self.session_id}[/yellow]\n"
            f"Total Cycles: [green]{report['total_cycles']}[/green]",
            title="Summary",
        ))

        table = Table(title="Performance Metrics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        ps = report["planner_stats"]
        rs = report["resource_stats"]
        rc = report["recovery_stats"]
        ex = report["executor_stats"]

        table.add_row("Avg Plan Latency", f"{ps.get('avg_latency_ms', 0):.0f} ms")
        table.add_row("Avg Tokens/Plan", f"{ps.get('avg_tokens_per_plan', 0):.0f}")
        table.add_row("Peak RAM", f"{rs.get('ram_peak_percent', 0):.1f}%")
        table.add_row("Avg Power", f"{rs.get('estimated_avg_power_w', 0):.2f} W")
        table.add_row("Sensor Failures", f"{rc.get('total_failures', 0)}")
        table.add_row("Recovery Success Rate", f"{rc.get('success_rate_pct', 100):.1f}%")
        table.add_row("Steps Split (RAD)", f"{ex.get('total_splits', 0)}")
        table.add_row("Split Rate", f"{ex.get('split_rate_pct', 0):.1f}%")

        console.print(table)
