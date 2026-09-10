import time
import random
from dataclasses import dataclass
from typing import Optional, Callable
from enum import Enum

from config.settings import SIM_ACTUATOR_LATENCY_MS
from core.logger import get_logger

logger = get_logger("ActuatorSimulator")

class ActuatorState(Enum):
    OFF = "OFF"
    ON  = "ON"

@dataclass
class ActuatorResult:
    actuator_id: str
    command: str
    success: bool
    latency_ms: float
    state_after: ActuatorState
    message: str
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "actuator_id": self.actuator_id,
            "command": self.command,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 2),
            "state_after": self.state_after.value,
            "message": self.message,
            "timestamp": self.timestamp,
        }

class ActuatorSimulator:
    def __init__(self, sensor_simulator=None):
        self._sensor_sim = sensor_simulator
        self._states = {
            "relay":   ActuatorState.OFF,
            "buzzer":  ActuatorState.OFF,
            "led_red": ActuatorState.OFF,
            "led_grn": ActuatorState.ON,  
            "led_yel": ActuatorState.OFF,
            "ble":     ActuatorState.OFF,
        }
        self._alert_log: list = []
        logger.info("[actuator] ActuatorSimulator initialized — all actuators ready")

    def _simulate_latency(self) -> float:
        latency = SIM_ACTUATOR_LATENCY_MS + random.uniform(-5, 5)
        time.sleep(latency / 1000.0)
        return latency

    def execute(self, actuator_id: str, command: str, payload: Optional[dict] = None) -> ActuatorResult:
        if actuator_id not in self._states:
            return ActuatorResult(
                actuator_id=actuator_id, command=command,
                success=False, latency_ms=0.0,
                state_after=ActuatorState.OFF,
                message=f"Unknown actuator: {actuator_id}",
                timestamp=time.time(),
            )

        t0 = time.time()
        latency = self._simulate_latency()
        success = True
        message = ""

        if command == "ON":
            self._states[actuator_id] = ActuatorState.ON
            message = f"{actuator_id} activated"
            logger.info("[actuator] ⚡ %s → ON", actuator_id.upper())

        elif command == "OFF":
            self._states[actuator_id] = ActuatorState.OFF
            message = f"{actuator_id} deactivated"
            logger.info("[actuator] ⭕ %s → OFF", actuator_id.upper())

            if actuator_id == "relay" and self._sensor_sim:
                self._sensor_sim.inject_recovery()
                logger.info("[actuator] 🔄 Motor shutdown — sensor simulator recovery triggered")

        elif command == "ALERT":
            msg = (payload or {}).get("message", "FAULT DETECTED")
            severity = (payload or {}).get("severity", "CRITICAL")
            self._states[actuator_id] = ActuatorState.ON
            self._alert_log.append({
                "timestamp": time.time(),
                "message": msg,
                "severity": severity,
            })
            message = f"BLE alert sent: [{severity}] {msg}"
            logger.warning("[actuator] 📡 BLE ALERT: [%s] %s", severity, msg)

        elif command == "BLINK":
            message = f"{actuator_id} blink sequence executed"
            logger.info("[actuator] 💡 %s → BLINK", actuator_id.upper())

        else:
            success = False
            message = f"Unknown command: {command}"

        return ActuatorResult(
            actuator_id=actuator_id,
            command=command,
            success=success,
            latency_ms=latency,
            state_after=self._states[actuator_id],
            message=message,
            timestamp=time.time(),
        )

    def execute_sequence(self, sequence: list) -> list:
        results = []
        for cmd in sequence:
            result = self.execute(
                actuator_id=cmd.get("actuator_id", ""),
                command=cmd.get("command", ""),
                payload=cmd.get("payload"),
            )
            results.append(result)
            if not result.success:
                logger.error("[actuator] Sequence aborted at: %s", cmd)
                break
        return results

    def get_all_states(self) -> dict:
        return {k: v.value for k, v in self._states.items()}

    @property
    def alert_log(self) -> list:
        return self._alert_log
