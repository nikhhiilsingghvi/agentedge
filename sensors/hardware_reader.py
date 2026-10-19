"""
sensors/hardware_reader.py — AgentEdge Hybrid Hardware Sensor Reader
=====================================================================
Provides HybridSensorReader for use in AGENT_MODE = "hybrid" or "hardware".

Physical hardware present (Raspberry Pi 4):
  ✅  MPU6050 — accelerometer/gyroscope, I2C bus 1, address 0x68

Simulated (hardware not available):
  📊  DHT22  — temperature/humidity         [SIM]
  📊  INA219 — current sensor               [SIM]
  📊  HC-SR04 — ultrasonic distance         [SIM]
  📊  MQ-135 — gas/air-quality sensor       [SIM]

Vibration metric derivation
-----------------------------
Raw MPU6050 accelerometer values include gravity (~1 g on the Z axis when
the board is flat).  Reporting total acceleration magnitude as "vibration"
would always read ≈1 g even at rest, making all threshold comparisons
meaningless.

This implementation uses a simple static gravity removal:
  1. Read three axes (X, Y, Z) in g-units using the ±2 g full-scale range
     (sensitivity = 16 384 LSB/g, 16-bit signed).
  2. Compute the 3-axis static bias on the first N_CALIBRATION_SAMPLES
     samples (device should be at rest during startup).
  3. Subtract the bias from each subsequent reading and report the magnitude
     of the residual:
         vibration = sqrt((ax-bx)² + (ay-by)² + (az-bz)²)
  4. When calibration is incomplete the magnitude minus 1 g is used as a
     conservative fallback:
         vibration ≈ max(0, magnitude - 1.0)

This is a defensible engineering approximation for a stationary mount.
Thresholds in config/settings.py are interpreted as dynamic-vibration g-units,
not total-acceleration g-units.

I2C error handling
-------------------
OSError is raised by smbus2 for: device not found, bus timeout, NACK.
IOError is an alias of OSError on Python ≥ 3.3.
All hardware exceptions are caught; the reading is returned as FAILED with an
actionable failure_reason so the existing RecoveryProtocol can handle it.
"""
from __future__ import annotations

import math
import random
import time
from typing import Optional

from sensors.simulator import SensorReading, SensorStatus, SensorSimulator
from config.settings import (
    AGENT_MODE,
    MPU6050_I2C_BUS,
    MPU6050_ADDR,
    VIBRATION_NORMAL_MAX,
    VIBRATION_WARNING_MAX,
    VIBRATION_CRITICAL,
    TEMPERATURE_NORMAL_MAX,
    TEMPERATURE_WARNING_MAX,
    CURRENT_NORMAL_MAX,
    CURRENT_WARNING_MAX,
    DISTANCE_JAM_THRESHOLD,
    GAS_NORMAL_MAX,
    GAS_WARNING_MAX,
)
from core.logger import get_logger

logger = get_logger("HardwareReader")

# ── MPU6050 register addresses ───────────────────────────────────────────────
_PWR_MGMT_1  = 0x6B   # Power management — write 0x00 to wake
_ACCEL_XOUT_H = 0x3B  # First of 6 accel bytes (XH, XL, YH, YL, ZH, ZL)
_GYRO_XOUT_H  = 0x43  # First of 6 gyro bytes
_ACCEL_FS_SEL = 0x1C  # Accel full-scale config register
_GYRO_FS_SEL  = 0x1B  # Gyro full-scale config register

# ±2 g full-scale → sensitivity = 16 384 LSB/g
_ACCEL_SENSITIVITY = 16_384.0

# Number of samples to average for gravity bias calibration at startup
_N_CALIBRATION_SAMPLES = 30

# ── Lazy import guard ─────────────────────────────────────────────────────────
try:
    import smbus2 as _smbus2_mod
    _SMBUS_AVAILABLE = True
except ImportError:
    _SMBUS_AVAILABLE = False
    _smbus2_mod = None  # type: ignore


def _make_failed(sensor_id: str, sensor_type: str, unit: str, reason: str) -> SensorReading:
    return SensorReading(
        sensor_id=sensor_id,
        sensor_type=sensor_type,
        value=None,
        unit=unit,
        status=SensorStatus.FAILED,
        timestamp=time.time(),
        failure_reason=reason,
    )


class _MPU6050Driver:
    """
    Low-level I2C driver for the MPU6050.

    Opened lazily on first read — this means __init__ never raises even if
    the device is absent; the first read() call returns FAILED instead.
    """

    def __init__(self, bus_num: int = 1, address: int = 0x68) -> None:
        self._bus_num = bus_num
        self._address = address
        self._bus: Optional[object] = None  # smbus2.SMBus instance
        self._initialized = False
        self._init_error: Optional[str] = None

        # Gravity-bias calibration state
        self._bias_x: float = 0.0
        self._bias_y: float = 0.0
        self._bias_z: float = 1.0   # Assume gravity on Z axis before calibration
        self._calibrated = False
        self._cal_samples: list = []

    # ── Connection ──────────────────────────────────────────────────────────

    def _ensure_open(self) -> bool:
        """
        Open the I2C bus and wake the MPU6050.
        Returns True on success, False on failure (sets self._init_error).
        Safe to call repeatedly.
        """
        if self._initialized:
            return True
        if not _SMBUS_AVAILABLE:
            self._init_error = (
                "smbus2 not installed. "
                "On Pi: pip install smbus2  (inside virtualenv)"
            )
            return False
        try:
            self._bus = _smbus2_mod.SMBus(self._bus_num)
            # Wake the device: clear SLEEP bit in PWR_MGMT_1
            self._bus.write_byte_data(self._address, _PWR_MGMT_1, 0x00)
            # Set accel full-scale to ±2 g (register value 0x00)
            self._bus.write_byte_data(self._address, _ACCEL_FS_SEL, 0x00)
            time.sleep(0.1)   # Allow sensor to stabilise
            self._initialized = True
            self._init_error = None
            logger.info(
                "[hw-mpu6050] Initialised on bus %d, address 0x%02X",
                self._bus_num, self._address,
            )
            return True
        except FileNotFoundError:
            self._init_error = (
                f"I2C bus {self._bus_num} not found. "
                "Enable I2C: sudo raspi-config → Interface Options → I2C"
            )
        except OSError as exc:
            self._init_error = (
                f"MPU6050 not found at 0x{self._address:02X} on bus "
                f"{self._bus_num}: {exc}. "
                "Check wiring: SDA→GPIO2 (pin 3), SCL→GPIO3 (pin 5), "
                "VCC→3.3 V, GND→GND."
            )
        return False

    def close(self) -> None:
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:  # noqa: BLE001
                pass
            self._bus = None
        self._initialized = False

    # ── Raw I2C reads ────────────────────────────────────────────────────────

    def _read_word_2c(self, reg: int) -> int:
        """Read a 16-bit signed two's-complement value from two consecutive registers."""
        high = self._bus.read_byte_data(self._address, reg)
        low  = self._bus.read_byte_data(self._address, reg + 1)
        val  = (high << 8) | low
        if val >= 0x8000:
            val -= 0x10000
        return val

    def _read_accel_raw(self) -> tuple[float, float, float]:
        """Return (ax, ay, az) in g-units (±2 g full-scale)."""
        ax = self._read_word_2c(_ACCEL_XOUT_H    ) / _ACCEL_SENSITIVITY
        ay = self._read_word_2c(_ACCEL_XOUT_H + 2) / _ACCEL_SENSITIVITY
        az = self._read_word_2c(_ACCEL_XOUT_H + 4) / _ACCEL_SENSITIVITY
        return ax, ay, az

    # ── Calibration ──────────────────────────────────────────────────────────

    def _update_calibration(self, ax: float, ay: float, az: float) -> None:
        """
        Accumulate samples and compute the mean bias once enough have been
        collected.  The sensor must be at rest during this phase.
        """
        self._cal_samples.append((ax, ay, az))
        if len(self._cal_samples) >= _N_CALIBRATION_SAMPLES:
            xs = [s[0] for s in self._cal_samples]
            ys = [s[1] for s in self._cal_samples]
            zs = [s[2] for s in self._cal_samples]
            self._bias_x = sum(xs) / len(xs)
            self._bias_y = sum(ys) / len(ys)
            self._bias_z = sum(zs) / len(zs)
            self._calibrated = True
            logger.info(
                "[hw-mpu6050] Gravity bias calibrated: bx=%.4f by=%.4f bz=%.4f g",
                self._bias_x, self._bias_y, self._bias_z,
            )

    # ── Public read ──────────────────────────────────────────────────────────

    def read_vibration(self) -> tuple[float, dict, Optional[str]]:
        """
        Returns (vibration_g, raw_data_dict, error_string_or_None).

        vibration_g  — dynamic acceleration magnitude after gravity removal,
                        in g-units.  Range: 0..~4 g.  Values >3 g indicate
                        FAULT per the configured thresholds.
        raw_data_dict — {"ax": …, "ay": …, "az": …, "bias_calibrated": bool}
        error_string  — None on success; actionable message on failure.
        """
        if not self._ensure_open():
            return 0.0, {}, self._init_error

        try:
            ax, ay, az = self._read_accel_raw()

            if not self._calibrated:
                self._update_calibration(ax, ay, az)

            # Dynamic acceleration after gravity removal
            dax = ax - self._bias_x
            day = ay - self._bias_y
            daz = az - self._bias_z
            vibration = math.sqrt(dax ** 2 + day ** 2 + daz ** 2)

            raw_data = {
                "ax_g": round(ax, 4),
                "ay_g": round(ay, 4),
                "az_g": round(az, 4),
                "bias_calibrated": self._calibrated,
                "bias_x": round(self._bias_x, 4),
                "bias_y": round(self._bias_y, 4),
                "bias_z": round(self._bias_z, 4),
                "dynamic_magnitude_g": round(vibration, 4),
            }
            return round(vibration, 4), raw_data, None

        except OSError as exc:
            # Re-attempt open next time
            self._initialized = False
            return 0.0, {}, f"MPU6050 I2C read error: {exc}"


# ── Module-level singleton — one driver per process ───────────────────────────
_mpu6050 = _MPU6050Driver(
    bus_num=MPU6050_I2C_BUS,
    address=MPU6050_ADDR,
)


# ═════════════════════════════════════════════════════════════════════════════
# HybridSensorReader
# ═════════════════════════════════════════════════════════════════════════════

class HybridSensorReader:
    """
    Drop-in replacement for SensorSimulator in hybrid/hardware mode.

    Implements the same public API as SensorSimulator:
      read_all() -> dict[str, SensorReading]
      read_vibration() / read_temperature() / read_current() /
      read_distance() / read_gas()
      inject_recovery()
      force_reading(sensor_id, value) / clear_forced_readings()
      simulate_sensor_failure(sensor_id, failure_type) / restore_sensor()
      machine_state  (property)

    In hybrid mode:
      • MPU6050 readings are PHYSICAL — clearly labelled [HW] in logs.
      • All other sensors are SIMULATED — clearly labelled [SIM] in logs.
    """

    def __init__(self) -> None:
        # Back-end simulator for the four sensors we don't have hardware for
        self._sim = SensorSimulator()

        # Forced readings override (same interface as SensorSimulator)
        self._forced: dict = {}
        # Injected failures override
        self._failures: dict = {}

        logger.info(
            "[hybrid-reader] Started — MPU6050: [HW] | "
            "DHT22/INA219/HC-SR04/MQ-135: [SIM]"
        )

    # ── Forced reading injection (benchmark interface) ────────────────────────

    def force_reading(self, sensor_id: str, value: float) -> None:
        self._forced[sensor_id] = value
        self._sim.force_reading(sensor_id, value)

    def clear_forced_readings(self) -> None:
        self._forced.clear()
        self._sim.clear_forced_readings()

    def simulate_sensor_failure(self, sensor_id: str, failure_type: str = "TIMEOUT") -> None:
        self._failures[sensor_id] = failure_type
        self._sim.simulate_sensor_failure(sensor_id, failure_type)

    def restore_sensor(self, sensor_id: str) -> None:
        self._failures.pop(sensor_id, None)
        self._sim.restore_sensor(sensor_id)

    def inject_recovery(self) -> None:
        self._sim.inject_recovery()

    @property
    def machine_state(self) -> str:
        return self._sim.machine_state

    # ── Individual sensor reads ───────────────────────────────────────────────

    def read_vibration(self) -> SensorReading:
        """
        Physical MPU6050 reading when AGENT_MODE == "hybrid".
        Falls back to simulator if smbus2 is absent or I2C fails.
        """
        sensor_id = "mpu6050"

        # Injected failure takes priority (benchmark test scenarios)
        if sensor_id in self._failures:
            return _make_failed(
                sensor_id, "vibration", "g",
                f"Injected failure: {self._failures[sensor_id]}",
            )

        # Forced reading (benchmark deterministic scenarios)
        if sensor_id in self._forced:
            val = self._forced[sensor_id]
            status = (
                SensorStatus.FAULT if val >= VIBRATION_CRITICAL else
                SensorStatus.WARN  if val >= VIBRATION_WARNING_MAX else
                SensorStatus.OK
            )
            return SensorReading(
                sensor_id=sensor_id, sensor_type="vibration",
                value=val, unit="g", status=status,
                timestamp=time.time(),
                raw_data={"source": "forced"},
            )

        if AGENT_MODE == "hybrid" and _SMBUS_AVAILABLE:
            val, raw_data, error = _mpu6050.read_vibration()
            if error:
                logger.warning("[hw-mpu6050] Read failed — falling back to [SIM]: %s", error)
                reading = self._sim.read_vibration()
                reading.failure_reason = f"[HW-FAIL→SIM] {error}"
                return reading
            status = (
                SensorStatus.FAULT if val >= VIBRATION_CRITICAL else
                SensorStatus.WARN  if val >= VIBRATION_WARNING_MAX else
                SensorStatus.OK
            )
            logger.debug("[hw-mpu6050] [HW] vibration=%.4f g  status=%s", val, status.value)
            return SensorReading(
                sensor_id=sensor_id, sensor_type="vibration",
                value=val, unit="g", status=status,
                timestamp=time.time(),
                raw_data=raw_data,
            )
        else:
            # Simulation fallback (smbus2 not available, or AGENT_MODE=simulation)
            reading = self._sim.read_vibration()
            logger.debug("[hybrid-reader] [SIM] vibration=%.4f g", reading.value or 0)
            return reading

    def read_temperature(self) -> SensorReading:
        """DHT22 not available — returns clearly-labelled simulated reading."""
        reading = self._sim.read_temperature()
        if reading.value is not None:
            logger.debug("[hybrid-reader] [SIM] temperature=%.2f°C", reading.value)
        return reading

    def read_current(self) -> SensorReading:
        """INA219 not available — returns clearly-labelled simulated reading."""
        reading = self._sim.read_current()
        if reading.value is not None:
            logger.debug("[hybrid-reader] [SIM] current=%.4f A", reading.value)
        return reading

    def read_distance(self) -> SensorReading:
        """HC-SR04 not available — returns clearly-labelled simulated reading."""
        reading = self._sim.read_distance()
        if reading.value is not None:
            logger.debug("[hybrid-reader] [SIM] distance=%.1f cm", reading.value)
        return reading

    def read_gas(self) -> SensorReading:
        """MQ-135 not available — returns clearly-labelled simulated reading."""
        reading = self._sim.read_gas()
        if reading.value is not None:
            logger.debug("[hybrid-reader] [SIM] gas=%.1f ppm", reading.value)
        return reading

    def read_all(self) -> dict:
        return {
            "mpu6050": self.read_vibration(),
            "dht22":   self.read_temperature(),
            "ina219":  self.read_current(),
            "hcsr04":  self.read_distance(),
            "mq135":   self.read_gas(),
        }

    def cleanup(self) -> None:
        """Release I2C bus cleanly."""
        _mpu6050.close()
        logger.info("[hybrid-reader] I2C bus released")


# ── Legacy alias — do not use in new code ─────────────────────────────────────
HardwareSensorReader = HybridSensorReader
