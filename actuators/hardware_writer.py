import time
from actuators.simulator import ActuatorResult, ActuatorState
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
    def __init__(self, sensor_simulator=None):
        self._states = {k: ActuatorState.OFF for k in GPIO_MAP}
        self._states["led_grn"] = ActuatorState.ON  
    def execute(self, actuator_id: str, command: str, payload=None) -> ActuatorResult:
        t0 = time.time()
        success = True
        message = ""

        try:
            if actuator_id in GPIO_MAP and command in ("ON", "OFF"):
                pin = GPIO_MAP[actuator_id]
                level = True if command == "ON" else False
                self._states[actuator_id] = ActuatorState.ON if level else ActuatorState.OFF
                message = f"{actuator_id} → {command}"

            elif actuator_id == "ble" and command == "ALERT":
                msg_text = (payload or {}).get("message", "ALERT")
                severity = (payload or {}).get("severity", "WARN")
                ble_msg = f"ALERT|{severity}|{msg_text}\n"
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
        pass
