"""
actuators/hardware_writer.py — AgentEdge Real GPIO Actuator Writer
===================================================================
Used in AGENT_MODE = "hybrid" or "hardware".

Available physical outputs (Raspberry Pi 4, BCM numbering):
  GPIO 23 — Buzzer (active buzzer or passive via PWM; see note below)
  GPIO 24 — LED red
  GPIO 25 — LED green
  GPIO 12 — LED yellow

NOT available / NOT implemented:
  relay  — No relay module present; returns explicit failure instead of
            silently reporting success.
  ble    — No hardware BLE module; BLE ALERT is logged only (software).

Wiring notes
-------------
LEDs:
  GPIO pin → 330 Ω resistor → LED anode (+) → LED cathode (−) → GND
  A 330 Ω resistor limits current to ≈10 mA at 3.3 V (well within the
  Pi 4's 16 mA per-pin maximum).  Never connect an LED directly without
  a current-limiting resistor.

Buzzer:
  A simple 3–5 V active buzzer can be driven directly from a GPIO pin if
  its rated current is ≤ 16 mA.  Most 3.3 V active buzzers draw 10–30 mA.
  If the buzzer draws > 16 mA, interpose a small NPN transistor (e.g.
  2N2222 or BC547):
    GPIO 23 → 1 kΩ resistor → transistor base
    buzzer + → 3.3 V or 5 V supply
    buzzer − → transistor collector
    transistor emitter → GND
  Check your buzzer's datasheet before wiring.

GPIO library
-------------
RPi.GPIO is used because it ships with Raspberry Pi OS and supports
Python 3.13.  It is imported lazily so that this module can be imported
on non-Pi machines without crashing; hardware calls will return FAILED
with an actionable message instead.

Safety rules enforced here
---------------------------
1. GPIO state is only updated in self._states AFTER the physical GPIO.output()
   call succeeds.  Callers always see the true state.
2. Unknown actuator IDs → explicit FAILED result (not silent success).
3. Unknown commands → explicit FAILED result.
4. relay → explicit FAILED result (no hardware available).
5. All outputs are turned OFF during cleanup().
6. Repeated cleanup() calls are safe (idempotent).
"""
from __future__ import annotations

import time
from typing import Optional

from actuators.simulator import ActuatorResult, ActuatorState
from config.settings import GPIO_PIN_MAP, AGENT_MODE
from core.logger import get_logger

logger = get_logger("HardwareWriter")

# ── Lazy GPIO import ──────────────────────────────────────────────────────────
try:
    import RPi.GPIO as _GPIO
    _GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    _GPIO = None          # type: ignore
    _GPIO_AVAILABLE = False

_GPIO_INITIALIZED = False   # module-level flag so repeated init is idempotent


def _init_gpio() -> Optional[str]:
    """
    Initialize RPi.GPIO in BCM mode.
    Returns None on success, or an error string on failure.
    Safe to call multiple times.
    """
    global _GPIO_INITIALIZED
    if _GPIO_INITIALIZED:
        return None
    if not _GPIO_AVAILABLE:
        return (
            "RPi.GPIO not installed. "
            "On Pi: pip install RPi.GPIO  (inside virtualenv)"
        )
    try:
        _GPIO.setmode(_GPIO.BCM)
        _GPIO.setwarnings(False)
        for name, pin in GPIO_PIN_MAP.items():
            _GPIO.setup(pin, _GPIO.OUT, initial=_GPIO.LOW)
            logger.debug("[hw-gpio] Pin %d (%s) configured as OUTPUT, initial LOW", pin, name)
        # Turn green LED on at startup (normal-operation indicator)
        if "led_grn" in GPIO_PIN_MAP:
            _GPIO.output(GPIO_PIN_MAP["led_grn"], _GPIO.HIGH)
        _GPIO_INITIALIZED = True
        logger.info("[hw-gpio] GPIO initialised — BCM mode, %d pins configured", len(GPIO_PIN_MAP))
        return None
    except Exception as exc:  # noqa: BLE001
        return f"GPIO init failed: {exc}"


def _cleanup_gpio() -> None:
    """Turn all configured outputs OFF and release GPIO resources."""
    global _GPIO_INITIALIZED
    if not _GPIO_AVAILABLE or not _GPIO_INITIALIZED:
        return
    try:
        for pin in GPIO_PIN_MAP.values():
            _GPIO.output(pin, _GPIO.LOW)
        _GPIO.cleanup()
        _GPIO_INITIALIZED = False
        logger.info("[hw-gpio] GPIO cleanup complete — all outputs LOW")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[hw-gpio] Cleanup error (non-fatal): %s", exc)


# ── Allowed actuators and commands ────────────────────────────────────────────
_GPIO_ACTUATORS = set(GPIO_PIN_MAP.keys())   # buzzer, led_red, led_grn, led_yel
_VALID_COMMANDS = {"ON", "OFF"}


class HardwareActuatorWriter:
    """
    Real GPIO actuator writer for Raspberry Pi 4.

    Implements the same public interface as ActuatorSimulator:
      execute(actuator_id, command, payload) -> ActuatorResult
      execute_sequence(sequence)              -> list[ActuatorResult]
      get_all_states()                        -> dict[str, str]
      cleanup()
    """

    def __init__(self, sensor_simulator=None) -> None:
        # sensor_simulator param accepted for API compatibility but unused
        self._states: dict[str, ActuatorState] = {
            k: ActuatorState.OFF for k in _GPIO_ACTUATORS
        }
        self._states["led_grn"] = ActuatorState.ON   # default: green = ON
        self._states["ble"] = ActuatorState.OFF
        self._alert_log: list = []

        self._init_error: Optional[str] = _init_gpio()
        if self._init_error:
            logger.error("[hw-gpio] %s", self._init_error)
        else:
            logger.info("[hw-gpio] HardwareActuatorWriter ready")

    def execute(
        self,
        actuator_id: str,
        command: str,
        payload: Optional[dict] = None,
    ) -> ActuatorResult:
        t0 = time.time()

        # ── relay: not available ──────────────────────────────────────────────
        if actuator_id == "relay":
            return ActuatorResult(
                actuator_id=actuator_id,
                command=command,
                success=False,
                latency_ms=(time.time() - t0) * 1000,
                state_after=ActuatorState.OFF,
                message=(
                    "relay not available: no relay module present in this hardware setup. "
                    "Motor control is simulated."
                ),
                timestamp=time.time(),
            )

        # ── BLE ALERT: software-only ──────────────────────────────────────────
        if actuator_id == "ble" and command == "ALERT":
            msg  = (payload or {}).get("message", "ALERT")
            sev  = (payload or {}).get("severity", "WARN")
            self._alert_log.append({"timestamp": time.time(), "message": msg, "severity": sev})
            self._states["ble"] = ActuatorState.ON
            logger.warning("[hw-gpio] BLE ALERT [%s]: %s  (software-only, no BLE hardware)", sev, msg)
            return ActuatorResult(
                actuator_id=actuator_id, command=command, success=True,
                latency_ms=(time.time() - t0) * 1000,
                state_after=ActuatorState.ON,
                message=f"BLE alert logged (software): [{sev}] {msg}",
                timestamp=time.time(),
            )

        # ── GPIO actuators ────────────────────────────────────────────────────
        if actuator_id not in _GPIO_ACTUATORS:
            return ActuatorResult(
                actuator_id=actuator_id, command=command, success=False,
                latency_ms=(time.time() - t0) * 1000,
                state_after=ActuatorState.OFF,
                message=f"Unknown actuator: {actuator_id!r}",
                timestamp=time.time(),
            )

        if command not in _VALID_COMMANDS:
            return ActuatorResult(
                actuator_id=actuator_id, command=command, success=False,
                latency_ms=(time.time() - t0) * 1000,
                state_after=self._states.get(actuator_id, ActuatorState.OFF),
                message=f"Unsupported command {command!r} for {actuator_id}",
                timestamp=time.time(),
            )

        # ── GPIO init failed ──────────────────────────────────────────────────
        if self._init_error:
            return ActuatorResult(
                actuator_id=actuator_id, command=command, success=False,
                latency_ms=(time.time() - t0) * 1000,
                state_after=self._states.get(actuator_id, ActuatorState.OFF),
                message=f"GPIO not available: {self._init_error}",
                timestamp=time.time(),
            )

        pin = GPIO_PIN_MAP[actuator_id]
        level = _GPIO.HIGH if command == "ON" else _GPIO.LOW
        new_state = ActuatorState.ON if command == "ON" else ActuatorState.OFF

        try:
            _GPIO.output(pin, level)
            # Only update state map AFTER the hardware call succeeds
            self._states[actuator_id] = new_state
            latency_ms = (time.time() - t0) * 1000
            logger.info(
                "[hw-gpio] %s → %s  (pin %d, %.1f ms)",
                actuator_id.upper(), command, pin, latency_ms,
            )
            return ActuatorResult(
                actuator_id=actuator_id, command=command, success=True,
                latency_ms=latency_ms,
                state_after=new_state,
                message=f"{actuator_id} → {command} (GPIO {pin})",
                timestamp=time.time(),
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.time() - t0) * 1000
            logger.error("[hw-gpio] GPIO error on %s (pin %d): %s", actuator_id, pin, exc)
            return ActuatorResult(
                actuator_id=actuator_id, command=command, success=False,
                latency_ms=latency_ms,
                state_after=self._states.get(actuator_id, ActuatorState.OFF),
                message=f"GPIO error on pin {pin}: {exc}",
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
                logger.error("[hw-gpio] Sequence aborted at: %s", cmd)
                break
        return results

    def get_all_states(self) -> dict:
        return {k: v.value for k, v in self._states.items()}

    @property
    def alert_log(self) -> list:
        return self._alert_log

    def cleanup(self) -> None:
        """Turn all outputs OFF and release GPIO resources. Idempotent."""
        _cleanup_gpio()
        for k in self._states:
            self._states[k] = ActuatorState.OFF
