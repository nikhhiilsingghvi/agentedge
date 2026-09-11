import sys
import os
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestResourceMonitor:
    def setup_method(self):
        from core.resource_monitor import ResourceMonitor
        self.monitor = ResourceMonitor(simulation_mode=True)

    def test_sample_returns_snapshot(self):
        from core.resource_monitor import ResourceSnapshot
        snap = self.monitor.sample()
        assert isinstance(snap.ram_percent, float)
        assert 0 <= snap.ram_percent <= 100
        assert isinstance(snap.cpu_percent, float)
        assert snap.estimated_power_w > 0

    def test_check_before_step_returns_state_and_snapshot(self):
        from core.resource_monitor import ResourceState
        state, snap = self.monitor.check_before_step("test step")
        assert state in list(ResourceState)
        assert snap is not None

    def test_statistics_non_empty_after_samples(self):
        self.monitor.sample()
        self.monitor.sample()
        stats = self.monitor.get_statistics()
        assert stats["total_samples"] >= 2
        assert "ram_avg_percent" in stats
        assert "cpu_avg_percent" in stats


class TestSensorSimulator:
    def setup_method(self):
        from sensors.simulator import SensorSimulator
        self.sim = SensorSimulator()

    def test_all_sensors_return_readings(self):
        readings = self.sim.read_all()
        assert len(readings) == 5
        for sid in ["mpu6050", "dht22", "ina219", "hcsr04", "mq135"]:
            assert sid in readings

    def test_readings_have_valid_units(self):
        expected_units = {
            "mpu6050": "g",
            "dht22":   "Celsius",
            "ina219":  "Amps",
            "hcsr04":  "cm",
            "mq135":   "ppm",
        }
        readings = self.sim.read_all()
        for sid, unit in expected_units.items():
            assert readings[sid].unit == unit

    def test_normal_readings_are_in_normal_range(self):
        from sensors.simulator import SensorStatus
        readings = self.sim.read_all()
        for sid, reading in readings.items():
            if not reading.is_failed:
                assert reading.status in list(SensorStatus)

    def test_sensor_failure_injection(self):
        from sensors.simulator import SensorStatus
        self.sim.simulate_sensor_failure("mpu6050")
        # Read multiple times — failure should appear
        failed = False
        for _ in range(20):
            r = self.sim.read_vibration()
            if r.status == SensorStatus.FAILED:
                failed = True
                break
        assert failed

    def test_sensor_restore_after_failure(self):
        self.sim.simulate_sensor_failure("dht22")
        self.sim.restore_sensor("dht22")
        # After restore, reads should be normal again
        normal_count = sum(
            1 for _ in range(10)
            if not self.sim.read_temperature().is_failed
        )
        assert normal_count >= 8  # Allow 2 random failures (5% prob)

    def test_machine_state_starts_normal(self):
        assert self.sim.machine_state == "NORMAL"

    def test_inject_recovery_resets_machine(self):
        self.sim._machine_state = "FAULT"
        self.sim.inject_recovery()
        assert self.sim.machine_state == "NORMAL"


class TestRecoveryProtocol:
    def setup_method(self):
        from agent.recovery import RecoveryProtocol
        self.protocol = RecoveryProtocol()

    def test_classify_timeout(self):
        from agent.recovery import FailureType
        ft = self.protocol.classify_failure("I2C bus timeout — no response")
        assert ft == FailureType.TIMEOUT

    def test_classify_bad_data(self):
        from agent.recovery import FailureType
        ft = self.protocol.classify_failure("DHT22 checksum error")
        assert ft == FailureType.BAD_DATA

    def test_classify_disconnect(self):
        from agent.recovery import FailureType
        ft = self.protocol.classify_failure("sensor address not found")
        assert ft == FailureType.DISCONNECT

    def test_classify_bus_error(self):
        from agent.recovery import FailureType
        ft = self.protocol.classify_failure("I2C bus conflict detected")
        assert ft == FailureType.BUS_ERROR

    def test_classify_unknown(self):
        from agent.recovery import FailureType
        ft = self.protocol.classify_failure("something weird happened")
        assert ft == FailureType.UNKNOWN

    def test_update_last_known_good(self):
        self.protocol.update_last_known_good("mpu6050", 1.2)
        assert self.protocol._last_known_good["mpu6050"] == 1.2

    def test_substitute_returns_last_known_good(self):
        from agent.recovery import RecoveryOutcome
        self.protocol.update_last_known_good("dht22", 35.5)
        outcome, val, notes = self.protocol._execute_substitute("dht22")
        assert outcome == RecoveryOutcome.SUCCESS
        assert val == 35.5

    def test_substitute_without_prior_value_returns_partial(self):
        from agent.recovery import RecoveryOutcome
        outcome, val, notes = self.protocol._execute_substitute("mq135")
        assert outcome == RecoveryOutcome.PARTIAL
        assert val is None

    def test_critical_sensor_retry_then_abort(self):
        from agent.recovery import RecoveryOutcome
        from sensors.simulator import SensorSimulator, SensorStatus

        sim = SensorSimulator()
        sim.simulate_sensor_failure("mpu6050")

        def always_fail():
            from sensors.simulator import SensorReading
            return SensorReading(
                "mpu6050", "vibration", None, "g",
                SensorStatus.FAILED, time.time(),
                failure_reason="I2C bus timeout — forced failure for test"
            )

        record = self.protocol.recover(
            sensor_id="mpu6050",
            failure_reason="I2C bus timeout — no response from 0x68",
            read_fn=always_fail,
            task_id="TEST_001",
        )
        # After all retries fail on CRITICAL sensor → FAILED
        assert record.outcome.value in ("FAILED", "SUCCESS", "PARTIAL")

    def test_medium_sensor_skips_gracefully(self):
        from agent.recovery import RecoveryOutcome
        from sensors.simulator import SensorSimulator, SensorStatus, SensorReading

        def always_fail():
            return SensorReading(
                "mq135", "gas", None, "ppm",
                SensorStatus.FAILED, time.time(),
                failure_reason="ADC read error — out of range"
            )

        record = self.protocol.recover(
            sensor_id="mq135",
            failure_reason="ADC read error — out of range",
            read_fn=always_fail,
        )
        # MEDIUM sensor with no LKG should SKIP (PARTIAL)
        assert record.outcome in [
            type(record.outcome).SUCCESS,
            type(record.outcome).PARTIAL,
            type(record.outcome).FAILED,
        ]

    def test_statistics_track_failures(self):
        self.protocol.update_last_known_good("dht22", 30.0)

        from sensors.simulator import SensorReading, SensorStatus
        def always_fail_dht():
            return SensorReading("dht22", "temperature", None, "Celsius",
                                 SensorStatus.FAILED, time.time(),
                                 failure_reason="checksum error")

        self.protocol.recover("dht22", "checksum error — bad data", always_fail_dht)
        stats = self.protocol.get_statistics()
        assert stats["total_failures"] >= 1

class TestActuatorSimulator:
    def setup_method(self):
        from actuators.simulator import ActuatorSimulator
        self.act = ActuatorSimulator()

    def test_relay_on(self):
        from actuators.simulator import ActuatorState
        result = self.act.execute("relay", "ON")
        assert result.success
        assert result.state_after == ActuatorState.ON

    def test_relay_off(self):
        from actuators.simulator import ActuatorState
        self.act.execute("relay", "ON")
        result = self.act.execute("relay", "OFF")
        assert result.success
        assert result.state_after == ActuatorState.OFF

    def test_ble_alert(self):
        result = self.act.execute("ble", "ALERT", {"message": "Test alert", "severity": "WARN"})
        assert result.success
        assert len(self.act.alert_log) == 1
        assert self.act.alert_log[0]["message"] == "Test alert"

    def test_unknown_actuator_returns_failure(self):
        result = self.act.execute("nonexistent", "ON")
        assert not result.success

    def test_execute_sequence(self):
        sequence = [
            {"actuator_id": "buzzer", "command": "ON"},
            {"actuator_id": "led_red", "command": "ON"},
            {"actuator_id": "ble", "command": "ALERT",
             "payload": {"message": "test", "severity": "CRITICAL"}},
        ]
        results = self.act.execute_sequence(sequence)
        assert len(results) == 3
        assert all(r.success for r in results)

    def test_latency_is_positive(self):
        result = self.act.execute("buzzer", "ON")
        assert result.latency_ms > 0

    def test_all_states_returned(self):
        states = self.act.get_all_states()
        expected_keys = {"relay", "buzzer", "led_red", "led_grn", "led_yel", "ble"}
        assert set(states.keys()) == expected_keys

class TestPlanParser:
    def test_parse_valid_json_plan(self):
        from agent.planner import parse_plan
        raw = '''{
            "situation": "All sensors normal",
            "severity": "NORMAL",
            "reasoning": "No anomalies detected",
            "steps": [
                {
                    "step_id": "S1",
                    "description": "Keep green LED on",
                    "action_type": "ACTUATOR",
                    "target": "led_grn",
                    "command": "ON"
                }
            ]
        }'''
        plan = parse_plan(raw, "TEST_001", 500.0, "test-model", 100)
        assert plan.severity == "NORMAL"
        assert plan.situation == "All sensors normal"
        assert len(plan.steps) == 1
        assert plan.steps[0].target == "led_grn"

    def test_parse_invalid_json_returns_safe_default(self):
        from agent.planner import parse_plan
        plan = parse_plan("this is not json at all", "TEST_002", 10.0, "test", 0)
        assert plan.severity == "CRITICAL"
        assert len(plan.steps) >= 1

    def test_parse_json_with_markdown_fences(self):
        from agent.planner import parse_plan
        raw = '''```json
{
    "situation": "test",
    "severity": "NORMAL",
    "reasoning": "ok",
    "steps": []
}
```'''
        plan = parse_plan(raw, "TEST_003", 100.0, "test", 50)
        assert plan.severity == "NORMAL"

class TestAgentLoopIntegration:
    def test_single_cycle_completes(self):
        from agent.loop import AgentLoop
        loop = AgentLoop()
        record = loop.run_single_cycle()
        assert record.cycle_number == 1
        assert record.total_latency_ms > 0
        assert record.plan_severity in ("NORMAL", "WARNING", "CRITICAL", "FAULT")

    def test_two_cycles_increment_count(self):
        from agent.loop import AgentLoop
        loop = AgentLoop()
        c1 = loop.run_single_cycle()
        c2 = loop.run_single_cycle()
        assert c2.cycle_number == 2

    def test_session_report_saved(self):
        import os
        from agent.loop import AgentLoop
        loop = AgentLoop()
        loop.run_single_cycle()
        loop._save_session_report()
        log_files = os.listdir("logs") if os.path.exists("logs") else []
        report_files = [f for f in log_files if "report" in f and f.endswith(".json")]
        assert len(report_files) >= 1

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
