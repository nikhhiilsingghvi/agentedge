"""
AgentEdge Sensor Simulator
===========================
Realistic simulation of all 5 industrial sensors.
Designed so the exact same agent code runs in simulation
and on real hardware — only the data source changes.

Sensor models:
  - MPU6050: Vibration (g-force) with machine fault injection
  - DHT22:   Temperature + Humidity with thermal ramp
  - INA219:  Motor current with overcurrent fault
  - HC-SR04: Ultrasonic distance (conveyor jam simulation)
  - MQ-135:  Gas / air quality (fume detection)

All sensors support:
  - Normal operating mode
  - Degraded / warning mode
  - Fault / critical mode
  - Simulated sensor failure (for recovery protocol testing)

Copyright (c) 2026 AgentEdge Project, DSCE ECE Dept.
"""

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
    OK      = "OK"
    WARN    = "WARN"
    FAULT   = "FAULT"
    FAILED  = "FAILED"   # Sensor hardware failure — triggers recovery protocol


@dataclass
class SensorReading:
    """
    Unified sensor reading structure.
    Identical format for simulation and real hardware.
    This abstraction is key to hardware portability.
    """
    sensor_id: str
    sensor_type: str
    value: Optional[float]
    unit: str
    status: SensorStatus
    timestamp: float
    raw_data: Dict[str, Any] = field(default_factory=dict)
    failure_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "sensor_id": self.sensor_id,
            "sensor_type": self.sensor_type,
            "value": round(self.value, 4) if self.value is not None else None,
            "unit": self.unit,
            "status": self.status.value,
            "timestamp": self.timestamp,
            "raw_data": self.raw_data,
            "failure_reason": self.failure_reason,
        }

    @property
    def is_failed(self) -> bool:
        return self.status == SensorStatus.FAILED

    @property
    def is_critical(self) -> bool:
        return self.status == SensorStatus.FAULT


class SensorSimulator:
    """
    Physics-informed sensor simulator for AgentEdge Phase 1.

    Simulates realistic machine behavior:
    - Normal idle operation with natural noise
    - Gradual degradation (fault ramp)
    - Sudden fault injection
    - Sensor hardware failure (for recovery protocol)

    The simulator uses a state machine per sensor so faults
    persist realistically rather than flickering randomly.
    """

    def __init__(self):
        self._start_time = time.time()
        self._machine_state = "NORMAL"  # NORMAL | DEGRADING | FAULT
        self._fault_countdown = 0
        self._degradation_factor = 1.0
        self._sensor_failures: Dict[str, bool] = {}

        # Randomise fault injection timing (10–60 seconds in)
        self._next_fault_time = time.time() + random.uniform(15, 45)

        logger.info("[simulator] Sensor simulator started — machine state: NORMAL")
        logger.info("[simulator] Fault injection scheduled in ~%.0f seconds",
                    self._next_fault_time - time.time())

    # ─── MACHINE STATE ENGINE ─────────────────────────────────────────────────

    def _update_machine_state(self) -> None:
        """Drive the machine through its fault lifecycle."""
        now = time.time()

        if self._machine_state == "NORMAL":
            if now >= self._next_fault_time:
                self._machine_state = "DEGRADING"
                self._degradation_factor = 1.0
                logger.warning("[simulator] 🔧 Machine entering DEGRADING state")

        elif self._machine_state == "DEGRADING":
            # Ramp degradation over 10 seconds
            elapsed = now - self._next_fault_time
            self._degradation_factor = min(1.0 + (elapsed / 10.0) * 2.5, 3.5)
            if self._degradation_factor >= 3.0:
                self._machine_state = "FAULT"
                logger.error("[simulator] 🚨 Machine entered FAULT state")

        elif self._machine_state == "FAULT":
            # Stay in fault until agent executes recovery
            pass

    def inject_recovery(self) -> None:
        """Called by actuator when agent executes motor shutdown."""
        self._machine_state = "NORMAL"
        self._degradation_factor = 1.0
        self._next_fault_time = time.time() + random.uniform(20, 60)
        logger.info("[simulator] ✅ Machine recovered — next fault in ~%.0f seconds",
                    self._next_fault_time - time.time())

    def simulate_sensor_failure(self, sensor_id: str) -> None:
        """Inject a sensor hardware failure for recovery protocol testing."""
        self._sensor_failures[sensor_id] = True
        logger.warning("[simulator] Sensor FAILURE injected: %s", sensor_id)

    def restore_sensor(self, sensor_id: str) -> None:
        """Restore a failed sensor (simulates reconnection)."""
        self._sensor_failures.pop(sensor_id, None)
        logger.info("[simulator] Sensor restored: %s", sensor_id)

    def _check_sensor_failure(self, sensor_id: str) -> bool:
        """Returns True if sensor is in failure state."""
        if sensor_id in self._sensor_failures:
            return True
        # Random spontaneous failure
        if random.random() < SIM_SENSOR_FAILURE_PROB:
            logger.warning("[simulator] Spontaneous sensor failure: %s", sensor_id)
            return True
        return False

    # ─── INDIVIDUAL SENSOR READERS ───────────────────────────────────────────

    def read_vibration(self) -> SensorReading:
        """
        MPU6050 — 6-axis IMU vibration reading.
        Returns resultant g-force magnitude.
        Normal: < 1.5g | Warning: 1.5–3.0g | Critical: > 3.0g
        """
        self._update_machine_state()

        if self._check_sensor_failure("mpu6050"):
            return SensorReading(
                sensor_id="mpu6050", sensor_type="vibration",
                value=None, unit="g",
                status=SensorStatus.FAILED,
                timestamp=time.time(),
                failure_reason="I2C bus timeout — no response from 0x68",
            )

        t = time.time() - self._start_time
        base_noise = random.gauss(0, 0.08)

        if self._machine_state == "NORMAL":
            # Natural vibration with periodic component
            val = 0.4 + 0.1 * math.sin(2 * math.pi * t / 5) + base_noise
        elif self._machine_state == "DEGRADING":
            val = 0.4 * self._degradation_factor + 0.3 * math.sin(2 * math.pi * t / 2) + abs(base_noise)
        else:  # FAULT
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
        """
        DHT22 — Temperature sensor reading.
        Normal: < 45°C | Warning: 45–60°C | Critical: > 60°C
        """
        if self._check_sensor_failure("dht22"):
            return SensorReading(
                sensor_id="dht22", sensor_type="temperature",
                value=None, unit="Celsius",
                status=SensorStatus.FAILED,
                timestamp=time.time(),
                failure_reason="DHT22 checksum error — bad data received",
            )

        t = time.time() - self._start_time
        ambient = 28.0

        if self._machine_state == "NORMAL":
            val = ambient + random.gauss(0, 0.5)
        elif self._machine_state == "DEGRADING":
            val = ambient + (self._degradation_factor - 1.0) * 18.0 + random.gauss(0, 0.8)
        else:  # FAULT
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
        """
        INA219 — Motor current reading.
        Normal: < 1.5A | Warning: 1.5–2.0A | Critical: > 2.0A
        Also used for energy measurement (mW = mA × mV).
        """
        if self._check_sensor_failure("ina219"):
            return SensorReading(
                sensor_id="ina219", sensor_type="current",
                value=None, unit="Amps",
                status=SensorStatus.FAILED,
                timestamp=time.time(),
                failure_reason="INA219 I2C address conflict — bus error",
            )

        if self._machine_state == "NORMAL":
            val = 0.8 + random.gauss(0, 0.05)
        elif self._machine_state == "DEGRADING":
            val = 0.8 + (self._degradation_factor - 1.0) * 0.7 + random.gauss(0, 0.08)
        else:  # FAULT
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
        """
        HC-SR04 — Ultrasonic distance sensor.
        Simulates conveyor belt with occasional jam (object too close).
        """
        if self._check_sensor_failure("hcsr04"):
            return SensorReading(
                sensor_id="hcsr04", sensor_type="distance",
                value=None, unit="cm",
                status=SensorStatus.FAILED,
                timestamp=time.time(),
                failure_reason="HC-SR04 timeout — no echo pulse received",
            )

        # Normal: object ~25–35cm away
        # Fault: object < 10cm (jam)
        if self._machine_state == "FAULT" and random.random() < 0.7:
            val = random.uniform(4.0, 9.5)  # Jam
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
        """
        MQ-135 — Air quality / gas sensor.
        Normal: < 200 ppm | Warning: 200–400 ppm | Critical: > 400 ppm
        """
        if self._check_sensor_failure("mq135"):
            return SensorReading(
                sensor_id="mq135", sensor_type="gas",
                value=None, unit="ppm",
                status=SensorStatus.FAILED,
                timestamp=time.time(),
                failure_reason="MQ-135 ADC read error — out of range",
            )

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
        """Read all sensors and return as dictionary keyed by sensor_id."""
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
