import random
import time
import math
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from enum import Enum

from config.settings import (
    VIBRATION_NORMAL_MAX, VIBRATION_WARNING_MAX, VIBRATION_CRITICAL,
    TEMPERATURE_NORMAL_MAX, TEMPERATURE_WARNING_MAX, TEMPERATURE_CRITICAL,
    CURRENT_NORMAL_MAX, CURRENT_WARNING_MAX, CURRENT_CRITICAL,
    DISTANCE_JAM_THRESHOLD,
    GAS_NORMAL_MAX, GAS_WARNING_MAX, GAS_CRITICAL,
    SIM_FAULT_PROBABILITY, SIM_SENSOR_FAILURE_PROB,
    SIMULATION_MODE,
)
from core.logger import get_logger

logger = get_logger("SensorSimulator")

class SensorStatus(Enum):
    OK         = "OK"
    WARN       = "WARN"
    FAULT      = "FAULT"
    FAILED     = "FAILED"
    SUBSTITUTE = "SUBSTITUTE"   # Phase 2: stale/recovered value — not a live reading

# Maps task-level failure_type → realistic failure_reason message per sensor
_FAILURE_REASON_MAP: Dict[str, Dict[str, str]] = {
    "mpu6050": {
        "TIMEOUT":    "I2C bus timeout — no response from 0x68",
        "DISCONNECT": "MPU6050 sensor address not found — physical disconnect",
        "BAD_DATA":   "MPU6050 checksum error — bad data received",
        "BUS_ERROR":  "I2C bus conflict detected on 0x68",
        "UNKNOWN":    "MPU6050 unknown error",
    },
    "dht22": {
        "TIMEOUT":    "DHT22 timeout — no response",
        "DISCONNECT": "DHT22 sensor address not found — physical disconnect",
        "BAD_DATA":   "DHT22 checksum error — bad data received",
        "BUS_ERROR":  "DHT22 I2C bus conflict",
        "UNKNOWN":    "DHT22 unknown error",
    },
    "ina219": {
        "TIMEOUT":    "INA219 I2C bus timeout — no response from 0x40",
        "DISCONNECT": "INA219 sensor address not found — physical disconnect",
        "BAD_DATA":   "INA219 out of range — bad data received",
        "BUS_ERROR":  "INA219 I2C address conflict — bus error",
        "UNKNOWN":    "INA219 unknown error",
    },
    "hcsr04": {
        "TIMEOUT":    "HC-SR04 timeout — no echo pulse received",
        "DISCONNECT": "HC-SR04 sensor address not found — physical disconnect",
        "BAD_DATA":   "HC-SR04 checksum error — bad data received",
        "BUS_ERROR":  "HC-SR04 bus conflict",
        "UNKNOWN":    "HC-SR04 unknown error",
    },
    "mq135": {
        "TIMEOUT":    "MQ-135 timeout — ADC not responding",
        "DISCONNECT": "MQ-135 sensor address not found — physical disconnect",
        "BAD_DATA":   "MQ-135 ADC read error — out of range",
        "BUS_ERROR":  "MQ-135 bus conflict detected",
        "UNKNOWN":    "MQ-135 unknown error",
    },
}

_DEFAULT_FAILURE_REASON: Dict[str, str] = {
    "mpu6050": "I2C bus timeout — no response from 0x68",
    "dht22":   "DHT22 checksum error — bad data received",
    "ina219":  "INA219 I2C address conflict — bus error",
    "hcsr04":  "HC-SR04 timeout — no echo pulse received",
    "mq135":   "MQ-135 ADC read error — out of range",
}


@dataclass
class SensorReading:
    sensor_id: str
    sensor_type: str
    value: Optional[float]
    unit: str
    status: SensorStatus
    timestamp: float
    raw_data: Dict[str, Any] = field(default_factory=dict)
    failure_reason: Optional[str] = None
    is_substitute: bool = False           # Phase 2: True when value is stale/recovered
    substitute_age_s: Optional[float] = None  # Age of substitute value in seconds

    def to_dict(self) -> dict:
        return {
            "sensor_id":       self.sensor_id,
            "sensor_type":     self.sensor_type,
            "value":           round(self.value, 4) if self.value is not None else None,
            "unit":            self.unit,
            "status":          self.status.value,
            "timestamp":       self.timestamp,
            "raw_data":        self.raw_data,
            "failure_reason":  self.failure_reason,
            "is_substitute":   self.is_substitute,
            "substitute_age_s": round(self.substitute_age_s, 1) if self.substitute_age_s else None,
        }

    @property
    def is_failed(self) -> bool:
        return self.status == SensorStatus.FAILED

    @property
    def is_critical(self) -> bool:
        return self.status == SensorStatus.FAULT


class SensorSimulator:
    def __init__(self):
        self._start_time = time.time()
        self._machine_state = "NORMAL"
        self._fault_countdown = 0
        self._degradation_factor = 1.0

        # Maps sensor_id -> failure_type string (e.g. "TIMEOUT", "DISCONNECT")
        self._sensor_failures: Dict[str, str] = {}

        # Phase 1: Deterministic forced readings for benchmark tasks
        # sensor_id -> exact float value overriding random simulation
        self._forced_readings: Dict[str, float] = {}

        self._next_fault_time = time.time() + random.uniform(15, 45)

        logger.info("[simulator] Sensor simulator started — machine state: NORMAL")
        logger.info("[simulator] Fault injection scheduled in ~%.0f seconds",
                    self._next_fault_time - time.time())

    # ── Phase 1: Deterministic Reading Injection ──────────────────────────────

    def force_reading(self, sensor_id: str, value: float) -> None:
        """
        Force a specific sensor value for deterministic benchmark execution.
        When set, read_<sensor>() returns this exact value instead of the
        random simulation, making every benchmark task reproducible.
        """
        self._forced_readings[sensor_id] = value
        logger.debug("[simulator] Forced reading: %s = %.4f", sensor_id, value)

    def clear_forced_readings(self) -> None:
        """Remove all forced readings — returns to random simulation mode."""
        self._forced_readings.clear()
        logger.debug("[simulator] Forced readings cleared")

    def reset_state(self) -> None:
        """
        Full state reset for task isolation between benchmark runs.
        Clears forced readings, injected failures, resets machine to NORMAL.
        """
        self._forced_readings.clear()
        self._sensor_failures.clear()
        self._machine_state = "NORMAL"
        self._degradation_factor = 1.0
        self._next_fault_time = time.time() + random.uniform(15, 45)
        logger.debug("[simulator] Sensor simulator state fully reset")

    # ── Machine State ─────────────────────────────────────────────────────────

    def _update_machine_state(self) -> None:
        now = time.time()

        if self._machine_state == "NORMAL":
            if now >= self._next_fault_time:
                self._machine_state = "DEGRADING"
                self._degradation_factor = 1.0
                logger.warning("[simulator] 🔧 Machine entering DEGRADING state")

        elif self._machine_state == "DEGRADING":
            elapsed = now - self._next_fault_time
            self._degradation_factor = min(1.0 + (elapsed / 10.0) * 2.5, 3.5)
            if self._degradation_factor >= 3.0:
                self._machine_state = "FAULT"
                logger.error("[simulator] 🚨 Machine entered FAULT state")

        elif self._machine_state == "FAULT":
            pass

    def inject_recovery(self) -> None:
        self._machine_state = "NORMAL"
        self._degradation_factor = 1.0
        self._next_fault_time = time.time() + random.uniform(20, 60)
        logger.info("[simulator] ✅ Machine recovered — next fault in ~%.0f seconds",
                    self._next_fault_time - time.time())

    # ── Phase 1: Parameterized Failure Injection ──────────────────────────────

    def simulate_sensor_failure(
        self,
        sensor_id: str,
        failure_type: str = "TIMEOUT",
    ) -> None:
        """
        Inject a sensor failure of a specific type for benchmark task D.

        Args:
            sensor_id:    One of mpu6050, dht22, ina219, hcsr04, mq135
            failure_type: TIMEOUT | DISCONNECT | BAD_DATA | BUS_ERROR | UNKNOWN
                          Default TIMEOUT for backward compatibility.
        """
        self._sensor_failures[sensor_id] = failure_type.upper()
        logger.warning("[simulator] Sensor FAILURE injected: %s | Type: %s",
                       sensor_id, failure_type.upper())

    def restore_sensor(self, sensor_id: str) -> None:
        self._sensor_failures.pop(sensor_id, None)
        logger.info("[simulator] Sensor restored: %s", sensor_id)

    def _get_failure_reason(self, sensor_id: str) -> Optional[str]:
        """
        Returns the failure_reason string if this sensor should fail, else None.
        Priority: injected failures > spontaneous random failures.
        Forced readings (force_reading()) suppress spontaneous failures so that
        benchmark / test scenarios always observe the value they requested.
        """
        if sensor_id in self._sensor_failures:
            failure_type = self._sensor_failures[sensor_id]
            return _FAILURE_REASON_MAP.get(sensor_id, {}).get(
                failure_type,
                _DEFAULT_FAILURE_REASON.get(sensor_id, "sensor error"),
            )
        # Don't inject spontaneous failures when a forced reading is active —
        # tests and benchmark scenarios need deterministic sensor values.
        if sensor_id in self._forced_readings:
            return None
        if random.random() < SIM_SENSOR_FAILURE_PROB:
            logger.warning("[simulator] Spontaneous sensor failure: %s", sensor_id)
            return _DEFAULT_FAILURE_REASON.get(sensor_id, "spontaneous error")
        return None

    # ── Sensor Read Methods ───────────────────────────────────────────────────

    def read_vibration(self) -> SensorReading:
        self._update_machine_state()

        failure_reason = self._get_failure_reason("mpu6050")
        if failure_reason:
            return SensorReading(
                sensor_id="mpu6050", sensor_type="vibration",
                value=None, unit="g",
                status=SensorStatus.FAILED,
                timestamp=time.time(),
                failure_reason=failure_reason,
            )

        if "mpu6050" in self._forced_readings:
            val = self._forced_readings["mpu6050"]
        else:
            t = time.time() - self._start_time
            base_noise = random.gauss(0, 0.08)
            if self._machine_state == "NORMAL":
                val = 0.4 + 0.1 * math.sin(2 * math.pi * t / 5) + base_noise
            elif self._machine_state == "DEGRADING":
                val = 0.4 * self._degradation_factor + 0.3 * math.sin(2 * math.pi * t / 2) + abs(base_noise)
            else:
                val = 3.2 + random.uniform(0.1, 0.8) + abs(base_noise)

        val = max(0.0, val)
        status = (SensorStatus.FAULT if val >= VIBRATION_CRITICAL
                  else SensorStatus.WARN if val >= VIBRATION_WARNING_MAX
                  else SensorStatus.OK)

        return SensorReading(
            sensor_id="mpu6050", sensor_type="vibration",
            value=round(val, 4), unit="g",
            status=status, timestamp=time.time(),
            raw_data={"ax": round(val * 0.6, 3), "ay": round(val * 0.5, 3), "az": round(9.81 + val * 0.1, 3)},
        )

    def read_temperature(self) -> SensorReading:
        failure_reason = self._get_failure_reason("dht22")
        if failure_reason:
            return SensorReading(
                sensor_id="dht22", sensor_type="temperature",
                value=None, unit="Celsius",
                status=SensorStatus.FAILED,
                timestamp=time.time(),
                failure_reason=failure_reason,
            )

        if "dht22" in self._forced_readings:
            val = self._forced_readings["dht22"]
        else:
            ambient = 28.0
            if self._machine_state == "NORMAL":
                val = ambient + random.gauss(0, 0.5)
            elif self._machine_state == "DEGRADING":
                val = ambient + (self._degradation_factor - 1.0) * 18.0 + random.gauss(0, 0.8)
            else:
                val = 62.0 + random.uniform(0, 5.0)

        val = max(15.0, val)
        status = (SensorStatus.FAULT if val >= TEMPERATURE_CRITICAL
                  else SensorStatus.WARN if val >= TEMPERATURE_WARNING_MAX
                  else SensorStatus.OK)

        return SensorReading(
            sensor_id="dht22", sensor_type="temperature",
            value=round(val, 2), unit="Celsius",
            status=status, timestamp=time.time(),
            raw_data={"temperature_c": round(val, 2), "humidity_pct": round(45.0 + random.gauss(0, 2), 1)},
        )

    def read_current(self) -> SensorReading:
        failure_reason = self._get_failure_reason("ina219")
        if failure_reason:
            return SensorReading(
                sensor_id="ina219", sensor_type="current",
                value=None, unit="Amps",
                status=SensorStatus.FAILED,
                timestamp=time.time(),
                failure_reason=failure_reason,
            )

        if "ina219" in self._forced_readings:
            val = self._forced_readings["ina219"]
        else:
            if self._machine_state == "NORMAL":
                val = 0.8 + random.gauss(0, 0.05)
            elif self._machine_state == "DEGRADING":
                val = 0.8 + (self._degradation_factor - 1.0) * 0.7 + random.gauss(0, 0.08)
            else:
                val = 2.3 + random.uniform(0, 0.4)

        val = max(0.0, val)
        voltage = 12.0 + random.gauss(0, 0.1)
        power_w = val * voltage
        status = (SensorStatus.FAULT if val >= CURRENT_CRITICAL
                  else SensorStatus.WARN if val >= CURRENT_WARNING_MAX
                  else SensorStatus.OK)

        return SensorReading(
            sensor_id="ina219", sensor_type="current",
            value=round(val, 4), unit="Amps",
            status=status, timestamp=time.time(),
            raw_data={"current_a": round(val, 4), "voltage_v": round(voltage, 3), "power_w": round(power_w, 3)},
        )

    def read_distance(self) -> SensorReading:
        failure_reason = self._get_failure_reason("hcsr04")
        if failure_reason:
            return SensorReading(
                sensor_id="hcsr04", sensor_type="distance",
                value=None, unit="cm",
                status=SensorStatus.FAILED,
                timestamp=time.time(),
                failure_reason=failure_reason,
            )

        if "hcsr04" in self._forced_readings:
            val = self._forced_readings["hcsr04"]
            status = (SensorStatus.FAULT if val < DISTANCE_JAM_THRESHOLD
                      else SensorStatus.OK)
        else:
            if self._machine_state == "FAULT" and random.random() < 0.7:
                val = random.uniform(4.0, 9.5)
                status = SensorStatus.FAULT
            else:
                val = random.uniform(22.0, 38.0)
                status = SensorStatus.OK

        return SensorReading(
            sensor_id="hcsr04", sensor_type="distance",
            value=round(val, 2), unit="cm",
            status=status, timestamp=time.time(),
            raw_data={"distance_cm": round(val, 2), "echo_us": round(val * 58.3, 1)},
        )

    def read_gas(self) -> SensorReading:
        failure_reason = self._get_failure_reason("mq135")
        if failure_reason:
            return SensorReading(
                sensor_id="mq135", sensor_type="gas",
                value=None, unit="ppm",
                status=SensorStatus.FAILED,
                timestamp=time.time(),
                failure_reason=failure_reason,
            )

        if "mq135" in self._forced_readings:
            val = self._forced_readings["mq135"]
        else:
            if self._machine_state == "NORMAL":
                val = 120.0 + random.gauss(0, 15)
            elif self._machine_state == "DEGRADING":
                val = 120.0 + (self._degradation_factor - 1.0) * 160.0 + random.gauss(0, 20)
            else:
                val = 420.0 + random.uniform(0, 80)

        val = max(50.0, val)
        status = (SensorStatus.FAULT if val >= GAS_CRITICAL
                  else SensorStatus.WARN if val >= GAS_WARNING_MAX
                  else SensorStatus.OK)

        return SensorReading(
            sensor_id="mq135", sensor_type="gas",
            value=round(val, 1), unit="ppm",
            status=status, timestamp=time.time(),
            raw_data={"ppm": round(val, 1), "adc_raw": int(val * 3.1)},
        )

    def read_all(self) -> Dict[str, SensorReading]:
        readings = {
            "mpu6050": self.read_vibration(),
            "dht22":   self.read_temperature(),
            "ina219":  self.read_current(),
            "hcsr04":  self.read_distance(),
            "mq135":   self.read_gas(),
        }
        return readings

    @property
    def machine_state(self) -> str:
        return self._machine_state
