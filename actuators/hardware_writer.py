"""
AgentEdge Hardware Actuator Writer
=====================================
Phase 2: Real Hardware Implementation
Replace actuators/simulator.py with this file on Raspberry Pi 5.

GPIO Pin Map:
  RELAY   → GPIO18 (BCM)  — controls motor power via relay module
  BUZZER  → GPIO23 (BCM)  — active buzzer
  LED_RED → GPIO24 (BCM)  — critical fault indicator
  LED_GRN → GPIO25 (BCM)  — normal status
  LED_YEL → GPIO12 (BCM)  — warning indicator
  BLE     → UART TX=GPIO14 → ESP32 (handles BLE to operator phone)

Install:
  pip install RPi.GPIO pyserial

Copyright (c) 2026 AgentEdge Project, DSCE ECE Dept.
"""

import time
from actuators.simulator import ActuatorResult, ActuatorState

# ── Uncomment on RPi5 ────────────────────────────────────────────────────────
# import RPi.GPIO as GPIO
# import serial

GPIO_MAP = {
    "relay":   18,
    "buzzer":  23,
    "led_red": 24,
    "led_grn": 25,
    "led_yel": 12,
}
BLE_UART_PORT  = "/dev/ttyS0"
BLE_UART_BAUD  = 115200


class HardwareActuatorWriter:
    """
    Real GPIO actuator writer for Raspberry Pi 5.
    Identical interface to ActuatorSimulator.
    """

    def __init__(self, sensor_simulator=None):
        # GPIO.setmode(GPIO.BCM)
        # for pin in GPIO_MAP.values():
        #     GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
        # self._uart = serial.Serial(BLE_UART_PORT, BLE_UART_BAUD, timeout=1)
        self._states = {k: ActuatorState.OFF for k in GPIO_MAP}
        self._states["led_grn"] = ActuatorState.ON  # Green ON at startup
        # GPIO.output(GPIO_MAP["led_grn"], GPIO.HIGH)

    def execute(self, actuator_id: str, command: str, payload=None) -> ActuatorResult:
        t0 = time.time()
        success = True
        message = ""

        try:
            if actuator_id in GPIO_MAP and command in ("ON", "OFF"):
                pin = GPIO_MAP[actuator_id]
                level = True if command == "ON" else False
                # GPIO.output(pin, GPIO.HIGH if level else GPIO.LOW)
                self._states[actuator_id] = ActuatorState.ON if level else ActuatorState.OFF
                message = f"{actuator_id} → {command}"

            elif actuator_id == "ble" and command == "ALERT":
                msg_text = (payload or {}).get("message", "ALERT")
                severity = (payload or {}).get("severity", "WARN")
                ble_msg = f"ALERT|{severity}|{msg_text}\n"
                # self._uart.write(ble_msg.encode())
                message = f"BLE sent: {ble_msg.strip()}"

        except Exception as e:
            success = False
            message = str(e)

        latency_ms = (time.time() - t0) * 1000
        return ActuatorResult(
            actuator_id=actuator_id,
            command=command,
            success=success,
            latency_ms=latency_ms,
            state_after=self._states.get(actuator_id, ActuatorState.OFF),
            message=message,
            timestamp=time.time(),
        )

    def execute_sequence(self, sequence: list) -> list:
        return [self.execute(**cmd) for cmd in sequence]

    def get_all_states(self) -> dict:
        return {k: v.value for k, v in self._states.items()}

    def cleanup(self):
        """Call on shutdown to reset all GPIO pins."""
        # GPIO.cleanup()
        pass
