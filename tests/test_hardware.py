"""
tests/test_hardware.py — Hardware integration tests for AgentEdge
==================================================================
All tests here use mocks/fakes for I2C and GPIO.
They run on any machine (no Pi required).

Tests cover:
 • MPU6050 register read and two's-complement conversion
 • Vibration metric derivation (gravity removal)
 • I2C error handling and FAILED reading propagation
 • HybridSensorReader: forced readings, failure injection, simulated sensors
 • HardwareActuatorWriter: GPIO commands, relay rejection, BLE logging,
   unknown actuator/command, GPIO init failure, cleanup
 • Mode selection from AGENT_MODE environment variable
 • Integration: HybridSensorReader feeds correct SensorReading to AgentLoop
"""
from __future__ import annotations

import os
import sys
import time
import types
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Force simulation mode for all tests in this file ─────────────────────────
os.environ["AGENT_MODE"] = "simulation"


# ═════════════════════════════════════════════════════════════════════════════
# Helpers: minimal smbus2 and RPi.GPIO stubs
# ═════════════════════════════════════════════════════════════════════════════

def _make_smbus2_stub(register_data: dict | None = None):
    """
    Build a fake smbus2 module.
    register_data maps register_address -> byte_value
    """
    data = register_data or {}

    class FakeSMBus:
        def __init__(self, bus):
            pass

        def write_byte_data(self, addr, reg, val):
            pass

        def read_byte_data(self, addr, reg):
            return data.get(reg, 0)

        def close(self):
            pass

    stub = types.ModuleType("smbus2")
    stub.SMBus = FakeSMBus
    return stub


def _make_failing_smbus2_stub(exc):
    """smbus2 stub whose SMBus() raises `exc` immediately."""
    class FailingSMBus:
        def __init__(self, bus):
            raise exc

    stub = types.ModuleType("smbus2")
    stub.SMBus = FailingSMBus
    return stub


def _make_gpio_stub():
    """Minimal RPi.GPIO stub that tracks state."""
    stub = types.ModuleType("RPi.GPIO")
    stub.BCM = "BCM"
    stub.OUT = "OUT"
    stub.HIGH = 1
    stub.LOW = 0
    stub._states = {}

    def setmode(m): pass
    def setwarnings(w): pass
    def setup(pin, direction, initial=0):
        stub._states[pin] = initial
    def output(pin, level):
        stub._states[pin] = level
    def cleanup():
        stub._states.clear()

    stub.setmode = setmode
    stub.setwarnings = setwarnings
    stub.setup = setup
    stub.output = output
    stub.cleanup = cleanup
    return stub


# ═════════════════════════════════════════════════════════════════════════════
# TestMPU6050Driver — low-level I2C driver unit tests
# ═════════════════════════════════════════════════════════════════════════════

class TestMPU6050Driver:
    """Unit tests for _MPU6050Driver using a fake smbus2 stub."""

    def _make_driver(self, register_data=None, fail_with=None):
        """Return a fresh _MPU6050Driver with smbus2 patched."""
        if fail_with is not None:
            fake_smbus = _make_failing_smbus2_stub(fail_with)
        else:
            fake_smbus = _make_smbus2_stub(register_data or {})

        with mock.patch.dict("sys.modules", {"smbus2": fake_smbus}):
            # Re-import to pick up the patched module
            import importlib
            import sensors.hardware_reader as hr_mod
            hr_mod._smbus2_mod = fake_smbus
            hr_mod._SMBUS_AVAILABLE = True

            driver = hr_mod._MPU6050Driver(bus_num=1, address=0x68)
            driver._bus = fake_smbus.SMBus(1)
            driver._initialized = True
            return driver

    def test_read_word_2c_positive(self):
        """Positive 16-bit value decoded correctly."""
        from sensors.hardware_reader import _MPU6050Driver
        driver = _MPU6050Driver.__new__(_MPU6050Driver)
        driver._address = 0x68

        # Simulate register pair: HIGH=0x04, LOW=0x00 → value = 0x0400 = 1024
        fake_bus = mock.MagicMock()
        fake_bus.read_byte_data.side_effect = lambda addr, reg: {
            0x3B: 0x04, 0x3C: 0x00
        }.get(reg, 0)
        driver._bus = fake_bus
        assert driver._read_word_2c(0x3B) == 0x0400

    def test_read_word_2c_negative(self):
        """Negative 16-bit two's-complement value decoded correctly."""
        from sensors.hardware_reader import _MPU6050Driver
        driver = _MPU6050Driver.__new__(_MPU6050Driver)
        driver._address = 0x68

        # 0xFC00 = 64512 unsigned → -1024 in two's complement
        fake_bus = mock.MagicMock()
        fake_bus.read_byte_data.side_effect = lambda addr, reg: {
            0x3B: 0xFC, 0x3C: 0x00
        }.get(reg, 0)
        driver._bus = fake_bus
        assert driver._read_word_2c(0x3B) == -1024

    def test_accel_sensitivity_2g_range(self):
        """±2 g full-scale: sensitivity = 16 384 LSB/g.
        A raw value of 16384 should map to 1.0 g."""
        from sensors.hardware_reader import _ACCEL_SENSITIVITY
        assert _ACCEL_SENSITIVITY == 16_384.0

    def test_vibration_magnitude_flat_board(self):
        """
        On a flat, stationary board: ax≈0, ay≈0, az≈1 g.
        After gravity removal (bias_z=1.0), dynamic vibration ≈ 0.
        """
        import math
        from sensors.hardware_reader import _MPU6050Driver, _ACCEL_SENSITIVITY
        driver = _MPU6050Driver.__new__(_MPU6050Driver)
        driver._address = 0x68
        driver._bias_x = 0.0
        driver._bias_y = 0.0
        driver._bias_z = 1.0
        driver._calibrated = True
        driver._cal_samples = []

        # Raw: ax=0, ay=0, az=16384 (= 1.0 g)
        raw_z = int(1.0 * _ACCEL_SENSITIVITY)
        fake_bus = mock.MagicMock()
        fake_bus.read_byte_data.side_effect = lambda addr, reg: {
            0x3B: 0x00, 0x3C: 0x00,   # ax high, low
            0x3D: 0x00, 0x3E: 0x00,   # ay high, low
            0x3F: (raw_z >> 8) & 0xFF, 0x40: raw_z & 0xFF,   # az high, low
        }.get(reg, 0)
        driver._bus = fake_bus
        driver._initialized = True

        val, raw_data, error = driver.read_vibration()
        assert error is None
        assert val == pytest.approx(0.0, abs=0.01), f"Expected ~0, got {val}"
        assert raw_data["bias_calibrated"] is True

    def test_vibration_magnitude_with_motion(self):
        """Simulated 1 g dynamic vibration on X axis should report ~1 g."""
        import math
        from sensors.hardware_reader import _MPU6050Driver, _ACCEL_SENSITIVITY
        driver = _MPU6050Driver.__new__(_MPU6050Driver)
        driver._address = 0x68
        driver._bias_x = 0.0
        driver._bias_y = 0.0
        driver._bias_z = 1.0
        driver._calibrated = True
        driver._cal_samples = []

        # ax=1 g, ay=0, az=1 g → dynamic (1,0,0) → magnitude=1.0
        raw_x = int(1.0 * _ACCEL_SENSITIVITY)
        raw_z = int(1.0 * _ACCEL_SENSITIVITY)
        fake_bus = mock.MagicMock()
        fake_bus.read_byte_data.side_effect = lambda addr, reg: {
            0x3B: (raw_x >> 8) & 0xFF, 0x3C: raw_x & 0xFF,
            0x3D: 0x00, 0x3E: 0x00,
            0x3F: (raw_z >> 8) & 0xFF, 0x40: raw_z & 0xFF,
        }.get(reg, 0)
        driver._bus = fake_bus
        driver._initialized = True

        val, _, error = driver.read_vibration()
        assert error is None
        assert val == pytest.approx(1.0, abs=0.01)

    def test_i2c_error_returns_failed_string(self):
        """OSError during read_vibration returns (0.0, {}, error_string)."""
        from sensors.hardware_reader import _MPU6050Driver
        driver = _MPU6050Driver.__new__(_MPU6050Driver)
        driver._address = 0x68
        driver._bias_x = driver._bias_y = driver._bias_z = 0.0
        driver._calibrated = True
        driver._cal_samples = []

        fake_bus = mock.MagicMock()
        fake_bus.read_byte_data.side_effect = OSError("I2C bus timeout")
        driver._bus = fake_bus
        driver._initialized = True

        val, raw_data, error = driver.read_vibration()
        assert error is not None
        assert "I2C" in error or "timeout" in error.lower() or "MPU6050" in error
        assert val == 0.0

    def test_init_failure_no_smbus2(self):
        """If smbus2 is absent, _ensure_open returns False with an actionable message."""
        import importlib
        import sensors.hardware_reader as hr_mod
        orig = hr_mod._SMBUS_AVAILABLE
        hr_mod._SMBUS_AVAILABLE = False
        try:
            from sensors.hardware_reader import _MPU6050Driver
            driver = _MPU6050Driver(bus_num=1, address=0x68)
            result = driver._ensure_open()
            assert result is False
            assert driver._init_error is not None
            assert "smbus2" in driver._init_error.lower()
        finally:
            hr_mod._SMBUS_AVAILABLE = orig

    def test_calibration_accumulates_samples(self):
        """Calibration bias is set after N_CALIBRATION_SAMPLES samples."""
        from sensors.hardware_reader import _MPU6050Driver, _N_CALIBRATION_SAMPLES
        driver = _MPU6050Driver.__new__(_MPU6050Driver)
        driver._calibrated = False
        driver._cal_samples = []
        driver._bias_x = driver._bias_y = driver._bias_z = 0.0

        for _ in range(_N_CALIBRATION_SAMPLES - 1):
            driver._update_calibration(0.0, 0.0, 1.0)
        assert not driver._calibrated

        driver._update_calibration(0.0, 0.0, 1.0)
        assert driver._calibrated
        assert driver._bias_z == pytest.approx(1.0, abs=0.001)


# ═════════════════════════════════════════════════════════════════════════════
# TestHybridSensorReader — HybridSensorReader mock-based tests
# ═════════════════════════════════════════════════════════════════════════════

class TestHybridSensorReader:
    """HybridSensorReader in simulation fallback mode (smbus2 absent)."""

    def setup_method(self):
        os.environ["AGENT_MODE"] = "simulation"
        import importlib
        import config.settings as cs
        importlib.reload(cs)
        from sensors.hardware_reader import HybridSensorReader
        self.reader = HybridSensorReader()

    def test_read_all_returns_five_sensors(self):
        readings = self.reader.read_all()
        assert set(readings.keys()) == {"mpu6050", "dht22", "ina219", "hcsr04", "mq135"}

    def test_all_readings_have_required_fields(self):
        from sensors.simulator import SensorReading, SensorStatus
        for sid, r in self.reader.read_all().items():
            assert isinstance(r, SensorReading), f"{sid}: not a SensorReading"
            assert isinstance(r.status, SensorStatus), f"{sid}: bad status"
            assert r.sensor_id == sid

    def test_forced_reading_returned_immediately(self):
        self.reader.force_reading("mpu6050", 2.5)
        r = self.reader.read_vibration()
        assert r.value == pytest.approx(2.5, abs=0.001)
        self.reader.clear_forced_readings()

    def test_forced_reading_cleared_returns_simulated(self):
        self.reader.force_reading("mpu6050", 5.0)
        self.reader.clear_forced_readings()
        r = self.reader.read_vibration()
        # Value should now be random from simulator, not 5.0
        # Just check it doesn't crash and returns a reading
        from sensors.simulator import SensorReading
        assert isinstance(r, SensorReading)

    def test_injected_failure_returns_failed_status(self):
        from sensors.simulator import SensorStatus
        self.reader.simulate_sensor_failure("mpu6050", "TIMEOUT")
        r = self.reader.read_vibration()
        assert r.status == SensorStatus.FAILED
        self.reader.restore_sensor("mpu6050")

    def test_restored_sensor_no_longer_fails(self):
        from sensors.simulator import SensorStatus
        self.reader.simulate_sensor_failure("mpu6050", "TIMEOUT")
        self.reader.restore_sensor("mpu6050")
        r = self.reader.read_vibration()
        # Should not be FAILED (may be OK/WARN/FAULT depending on simulated value)
        assert r.status != SensorStatus.FAILED

    def test_simulated_sensors_return_valid_values(self):
        from sensors.simulator import SensorStatus
        for method in ("read_temperature", "read_current", "read_distance", "read_gas"):
            r = getattr(self.reader, method)()
            # Simulated sensors should not be FAILED under normal conditions
            assert r.status != SensorStatus.FAILED, \
                f"{method} returned FAILED unexpectedly"

    def test_vibration_forced_warn_threshold(self):
        """Force value at WARN threshold, expect WARN status."""
        from sensors.simulator import SensorStatus
        from config.settings import VIBRATION_WARNING_MAX
        self.reader.force_reading("mpu6050", VIBRATION_WARNING_MAX + 0.01)
        r = self.reader.read_vibration()
        assert r.status in (SensorStatus.WARN, SensorStatus.FAULT)
        self.reader.clear_forced_readings()

    def test_inject_recovery_does_not_crash(self):
        self.reader.inject_recovery()  # Should not raise

    def test_machine_state_accessible(self):
        state = self.reader.machine_state
        assert state in ("NORMAL", "DEGRADING", "FAULT", "RECOVERY")

    def test_cleanup_does_not_raise(self):
        self.reader.cleanup()  # Should not raise even on non-Pi


# ═════════════════════════════════════════════════════════════════════════════
# TestHardwareActuatorWriter — GPIO writer mock-based tests
# ═════════════════════════════════════════════════════════════════════════════

class TestHardwareActuatorWriter:
    """HardwareActuatorWriter with RPi.GPIO stubbed out."""

    def setup_method(self):
        os.environ["AGENT_MODE"] = "hybrid"
        # Inject a fake RPi.GPIO before importing hardware_writer
        self._gpio_stub = _make_gpio_stub()
        import actuators.hardware_writer as hw
        hw._GPIO = self._gpio_stub
        hw._GPIO_AVAILABLE = True
        hw._GPIO_INITIALIZED = False

        from actuators.hardware_writer import HardwareActuatorWriter
        self.writer = HardwareActuatorWriter()

    def teardown_method(self):
        import actuators.hardware_writer as hw
        hw._GPIO_INITIALIZED = False
        os.environ["AGENT_MODE"] = "simulation"

    def test_led_on_command_succeeds(self):
        result = self.writer.execute("led_red", "ON")
        assert result.success, result.message
        from actuators.simulator import ActuatorState
        assert result.state_after == ActuatorState.ON

    def test_led_off_command_succeeds(self):
        self.writer.execute("led_red", "ON")
        result = self.writer.execute("led_red", "OFF")
        assert result.success, result.message
        from actuators.simulator import ActuatorState
        assert result.state_after == ActuatorState.OFF

    def test_buzzer_on_succeeds(self):
        result = self.writer.execute("buzzer", "ON")
        assert result.success, result.message

    def test_relay_command_returns_failure(self):
        """relay is not available — must return failure, not success."""
        result = self.writer.execute("relay", "OFF")
        assert not result.success
        assert "relay" in result.message.lower()

    def test_ble_alert_logged_success(self):
        result = self.writer.execute("ble", "ALERT", payload={"message": "test", "severity": "WARN"})
        assert result.success
        assert len(self.writer.alert_log) >= 1

    def test_unknown_actuator_returns_failure(self):
        result = self.writer.execute("unknown_device", "ON")
        assert not result.success
        assert "unknown" in result.message.lower() or "Unknown" in result.message

    def test_unknown_command_returns_failure(self):
        result = self.writer.execute("led_red", "BLINK")
        assert not result.success

    def test_state_not_updated_on_gpio_failure(self):
        """If GPIO.output raises, state map must NOT change."""
        import actuators.hardware_writer as hw
        from actuators.simulator import ActuatorState

        # Make GPIO.output raise
        hw._GPIO.output = mock.MagicMock(side_effect=RuntimeError("GPIO fault"))

        before = self.writer._states.get("led_red")
        result = self.writer.execute("led_red", "ON")
        after = self.writer._states.get("led_red")

        assert not result.success
        assert after == before, "State changed despite GPIO failure"

        # Restore
        hw._GPIO.output = self._gpio_stub.output

    def test_get_all_states_returns_dict(self):
        states = self.writer.get_all_states()
        assert isinstance(states, dict)
        assert "led_red" in states
        assert "buzzer" in states

    def test_cleanup_turns_all_off(self):
        self.writer.execute("led_red", "ON")
        self.writer.cleanup()
        # After cleanup, state map should all be OFF
        from actuators.simulator import ActuatorState
        for k, v in self.writer._states.items():
            assert v == ActuatorState.OFF, f"{k} not OFF after cleanup"

    def test_cleanup_idempotent(self):
        """Calling cleanup twice must not raise."""
        self.writer.cleanup()
        self.writer.cleanup()   # Should not raise

    def test_execute_sequence_stops_on_failure(self):
        """Sequence should abort at first failure and not process remaining items."""
        seq = [
            {"actuator_id": "relay", "command": "OFF"},    # will fail
            {"actuator_id": "led_grn", "command": "ON"},   # should not run
        ]
        results = self.writer.execute_sequence(seq)
        assert len(results) == 1
        assert not results[0].success


class TestHardwareActuatorWriterInitFailure:
    """Writer with GPIO init failure (library missing)."""

    def setup_method(self):
        import actuators.hardware_writer as hw
        hw._GPIO_AVAILABLE = False
        hw._GPIO_INITIALIZED = False

    def teardown_method(self):
        import actuators.hardware_writer as hw
        hw._GPIO_AVAILABLE = False
        hw._GPIO_INITIALIZED = False

    def test_commands_return_failure_when_gpio_unavailable(self):
        import actuators.hardware_writer as hw
        hw._GPIO_AVAILABLE = False
        hw._GPIO_INITIALIZED = False
        from actuators.hardware_writer import HardwareActuatorWriter
        writer = HardwareActuatorWriter()
        result = writer.execute("led_red", "ON")
        assert not result.success
        assert "GPIO" in result.message or "not" in result.message


# ═════════════════════════════════════════════════════════════════════════════
# TestModeSelection — AGENT_MODE configuration
# ═════════════════════════════════════════════════════════════════════════════

class TestModeSelection:
    def test_simulation_mode_keeps_simulation_mode_true(self):
        os.environ["AGENT_MODE"] = "simulation"
        import importlib
        import config.settings as cs
        importlib.reload(cs)
        assert cs.SIMULATION_MODE is True
        assert cs.AGENT_MODE == "simulation"

    def test_hybrid_mode_keeps_simulation_mode_true(self):
        """SIMULATION_MODE should be True in hybrid (only hardware mode disables it)."""
        os.environ["AGENT_MODE"] = "hybrid"
        import importlib
        import config.settings as cs
        importlib.reload(cs)
        assert cs.SIMULATION_MODE is True   # hybrid still uses simulated sensors
        assert cs.AGENT_MODE == "hybrid"

    def test_invalid_mode_raises_value_error(self):
        os.environ["AGENT_MODE"] = "banana"
        import importlib
        import config.settings as cs
        with pytest.raises(ValueError, match="AGENT_MODE"):
            importlib.reload(cs)

    def teardown_method(self, method):
        os.environ["AGENT_MODE"] = "simulation"
        import importlib
        import config.settings as cs
        importlib.reload(cs)


# ═════════════════════════════════════════════════════════════════════════════
# TestHybridReaderAgentLoopCompatibility
# ═════════════════════════════════════════════════════════════════════════════

class TestHybridReaderAgentLoopCompatibility:
    """
    Verify HybridSensorReader can feed AgentLoop._phase_perceive().
    No hardware required — uses simulation fallback.
    """

    def test_read_all_output_compatible_with_perceive_phase(self):
        """
        _phase_perceive() iterates readings and calls r.is_failed, r.value.
        HybridSensorReader.read_all() must return SensorReading objects with those properties.
        """
        os.environ["AGENT_MODE"] = "simulation"
        from sensors.hardware_reader import HybridSensorReader
        reader = HybridSensorReader()
        readings = reader.read_all()

        for sid, r in readings.items():
            # These two accesses mirror what _phase_perceive() does
            _ = r.is_failed
            _ = r.value
            _ = r.status.value   # used in cycle record

        reader.cleanup()

    def test_vibration_reading_has_expected_unit(self):
        os.environ["AGENT_MODE"] = "simulation"
        from sensors.hardware_reader import HybridSensorReader
        reader = HybridSensorReader()
        r = reader.read_vibration()
        assert r.unit == "g"
        reader.cleanup()
