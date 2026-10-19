"""
AgentEdge — Configuration Settings
====================================
AGENT_MODE controls which hardware is used:

  simulation  All sensors and actuators are simulated (default / CI safe).
  hybrid      Real MPU6050 over I2C + real GPIO LEDs/buzzer.
              All other sensors (DHT22, INA219, HC-SR04, MQ-135) remain
              simulated and are labelled [SIM] in logs.
  hardware    All hardware integrations active; fails clearly if a device
              is missing (not yet fully implemented — reserved for future).

Set via environment variable:
  export AGENT_MODE=hybrid   # or simulation / hardware
Or via CLI argument:  python main.py demo --mode hybrid

SIMULATION_MODE is kept for backwards compatibility with existing callers:
  SIMULATION_MODE=True  ⟺  AGENT_MODE in ("simulation", "hybrid")
"""
import os
from typing import Literal

# ── Operating Mode ───────────────────────────────────────────────────────────
AGENT_MODE: str = os.environ.get("AGENT_MODE", "simulation").lower()
if AGENT_MODE not in ("simulation", "hybrid", "hardware"):
    raise ValueError(
        f"Invalid AGENT_MODE={AGENT_MODE!r}. "
        "Choose 'simulation', 'hybrid', or 'hardware'."
    )

# Backwards-compatible flag — True unless we are in full hardware mode
SIMULATION_MODE: bool = AGENT_MODE != "hardware"

# ── LLM Provider ─────────────────────────────────────────────────────────────
LLM_PROVIDER: str = "gemini"
LLM_MODEL: str = "gemini-3.1-flash-lite"
LLM_MAX_TOKENS: int = 1024
LLM_TEMPERATURE: float = 0.1

OLLAMA_BASE_URL: str = "http://localhost:11434"
OLLAMA_MODEL: str = "tinyllama"

# ── Resource Monitor Thresholds ───────────────────────────────────────────────
RAM_WARN_THRESHOLD_PERCENT: float = 70.0
RAM_CRITICAL_THRESHOLD_PERCENT: float = 85.0
CPU_WARN_THRESHOLD_PERCENT: float = 80.0
MAX_PLAN_STEPS: int = 10

# ── Sensor Thresholds ─────────────────────────────────────────────────────────
# Vibration — metric is dynamic acceleration magnitude (gravity-removed), unit: g
# See sensors/hardware_reader.py for derivation details.
VIBRATION_NORMAL_MAX: float = 1.5
VIBRATION_WARNING_MAX: float = 3.0
VIBRATION_CRITICAL: float = 3.0

TEMPERATURE_NORMAL_MAX: float = 45.0
TEMPERATURE_WARNING_MAX: float = 60.0
TEMPERATURE_CRITICAL: float = 60.0

CURRENT_NORMAL_MAX: float = 1.5
CURRENT_WARNING_MAX: float = 2.0
CURRENT_CRITICAL: float = 2.0

DISTANCE_JAM_THRESHOLD: float = 10.0
GAS_NORMAL_MAX: float = 200.0
GAS_WARNING_MAX: float = 400.0
GAS_CRITICAL: float = 400.0

# ── Simulation Timing ─────────────────────────────────────────────────────────
SIM_SENSOR_UPDATE_INTERVAL: float = 2.0
SIM_FAULT_PROBABILITY: float = 0.15
SIM_SENSOR_FAILURE_PROB: float = 0.05
AGENT_LOOP_INTERVAL: float = 3.0
MAX_RECOVERY_ATTEMPTS: int = 3
RECOVERY_WAIT_SECONDS: float = 1.0

# ── Benchmark ────────────────────────────────────────────────────────────────
BENCHMARK_TASKS_FILE: str = "benchmark/tasks.json"
BENCHMARK_RESULTS_DIR: str = "logs/benchmark_results"

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR: str = "logs"
LOG_LEVEL: str = "DEBUG"
LOG_TO_FILE: bool = True
LOG_ROTATION_MB: int = 10

# ── Actuator Simulation ───────────────────────────────────────────────────────
SIM_ACTUATOR_LATENCY_MS: float = 50.0
SIM_IDLE_POWER_WATTS: float = 4.5
SIM_INFERENCE_POWER_WATTS: float = 7.2
SIM_GPIO_POWER_WATTS: float = 0.1

# ── GPIO Pin Map (BCM numbering) ──────────────────────────────────────────────
# Used by actuators/hardware_writer.py in hybrid/hardware mode.
# Relay is intentionally absent — not available in this hardware setup.
GPIO_PIN_MAP: dict = {
    "buzzer":  23,   # GPIO23 → NPN transistor base (if needed) or direct
    "led_red": 24,   # GPIO24 → 330Ω → LED anode → GND
    "led_grn": 25,   # GPIO25 → 330Ω → LED anode → GND
    "led_yel": 12,   # GPIO12 → 330Ω → LED anode → GND
}

# ── MPU6050 I2C ───────────────────────────────────────────────────────────────
MPU6050_I2C_BUS: int = 1        # I2C bus 1 on Pi 4 (pins 3 & 5)
MPU6050_ADDR: int = 0x68        # default; 0x69 if AD0 pulled HIGH

# ── Project Metadata ──────────────────────────────────────────────────────────
PROJECT_NAME: str = "AgentEdge"
PROJECT_VERSION: str = "1.1.0-hybrid"
INSTITUTION: str = "DSCE ECE Dept, Bengaluru"
HARDWARE_TARGET: str = "Raspberry Pi 4 (4 GB) — hybrid mode"
