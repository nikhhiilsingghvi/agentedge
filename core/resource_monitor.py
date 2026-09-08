import psutil
import time
from dataclasses import dataclass
from typing import Tuple
from enum import Enum

from config.settings import (
    RAM_WARN_THRESHOLD_PERCENT,
    RAM_CRITICAL_THRESHOLD_PERCENT,
    CPU_WARN_THRESHOLD_PERCENT,
    SIM_IDLE_POWER_WATTS,
    SIM_INFERENCE_POWER_WATTS,
)
from core.logger import get_logger

logger = get_logger("ResourceMonitor")


class ResourceState(Enum):
    NORMAL   = "NORMAL"   
    WARN     = "WARN"      
    CRITICAL = "CRITICAL" 

@dataclass
class ResourceSnapshot:
    timestamp: float
    ram_used_mb: float
    ram_total_mb: float
    ram_percent: float
    cpu_percent: float
    state: ResourceState
    estimated_power_w: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "ram_used_mb": round(self.ram_used_mb, 1),
            "ram_total_mb": round(self.ram_total_mb, 1),
            "ram_percent": round(self.ram_percent, 2),
            "cpu_percent": round(self.cpu_percent, 1),
            "state": self.state.value,
            "estimated_power_w": round(self.estimated_power_w, 3),
        }

class ResourceMonitor:
    def __init__(self, simulation_mode: bool = True):
        self.simulation_mode = simulation_mode
        self._history: list[ResourceSnapshot] = []
        self._inference_active: bool = False
        logger.info("[resource] ResourceMonitor initialized — mode: %s",
                    "SIMULATION" if simulation_mode else "HARDWARE")

    def set_inference_active(self, active: bool) -> None:
        self._inference_active = active

    def sample(self) -> ResourceSnapshot:
        ram = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)
        if self._inference_active:
            est_power = SIM_INFERENCE_POWER_WATTS * (cpu / 100.0) + SIM_IDLE_POWER_WATTS
        else:
            est_power = SIM_IDLE_POWER_WATTS + (cpu / 100.0) * 1.5
        if ram.percent >= RAM_CRITICAL_THRESHOLD_PERCENT or cpu >= CPU_WARN_THRESHOLD_PERCENT:
            state = ResourceState.CRITICAL
        elif ram.percent >= RAM_WARN_THRESHOLD_PERCENT:
            state = ResourceState.WARN
        else:
            state = ResourceState.NORMAL

        snapshot = ResourceSnapshot(
            timestamp=time.time(),
            ram_used_mb=ram.used / (1024 ** 2),
            ram_total_mb=ram.total / (1024 ** 2),
            ram_percent=ram.percent,
            cpu_percent=cpu,
            state=state,
            estimated_power_w=est_power,
        )
        self._history.append(snapshot)
        return snapshot

    def check_before_step(self, step_description: str) -> Tuple[ResourceState, ResourceSnapshot]:
        snap = self.sample()

        if snap.state == ResourceState.CRITICAL:
            logger.warning(
                "[resource] ⚠️  CRITICAL — RAM: %.1f%% CPU: %.1f%% — "
                "SPLITTING step: '%s'",
                snap.ram_percent, snap.cpu_percent, step_description
            )
        elif snap.state == ResourceState.WARN:
            logger.warning(
                "[resource] ⚡ WARN — RAM: %.1f%% CPU: %.1f%% — step: '%s'",
                snap.ram_percent, snap.cpu_percent, step_description
            )
        else:
            logger.debug(
                "[resource] ✅ NORMAL — RAM: %.1f%% CPU: %.1f%%",
                snap.ram_percent, snap.cpu_percent
            )

        return snap.state, snap

    def get_statistics(self) -> dict:
        if not self._history:
            return {}

        rams = [s.ram_percent for s in self._history]
        cpus = [s.cpu_percent for s in self._history]
        powers = [s.estimated_power_w for s in self._history]
        criticals = sum(1 for s in self._history if s.state == ResourceState.CRITICAL)
        warns = sum(1 for s in self._history if s.state == ResourceState.WARN)

        return {
            "total_samples": len(self._history),
            "ram_avg_percent": round(sum(rams) / len(rams), 2),
            "ram_peak_percent": round(max(rams), 2),
            "cpu_avg_percent": round(sum(cpus) / len(cpus), 2),
            "cpu_peak_percent": round(max(cpus), 2),
            "critical_events": criticals,
            "warn_events": warns,
            "estimated_avg_power_w": round(sum(powers) / len(powers), 3),
            "estimated_total_energy_mj": round(
                sum(p * 0.1 * 1000 for p in powers), 2  # P*dt*1000 = mJ, dt=0.1s
            ),
        }
