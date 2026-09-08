# AgentEdge

**Agentic AI Planning on Resource-Constrained Embedded Systems**

> First benchmarked agentic AI system running entirely on embedded hardware, where a quantized small language model plans and executes real-world sensor tasks with failure recovery - no cloud required.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Phase](https://img.shields.io/badge/Phase-1%20Simulation-orange)](docs/SETUP_GUIDE.md)
[![Target](https://img.shields.io/badge/Target-IEEE%20Publication-red)](https://ieee.org)
[![Hardware](https://img.shields.io/badge/Hardware-Raspberry%20Pi%205-brightgreen)](https://raspberrypi.com)

---

## Overview

AgentEdge is a **research-grade embedded agentic AI system** that runs a Small Language Model (SLM) on a Raspberry Pi 5 to autonomously monitor an industrial factory machine and respond to sensor faults — completely offline, with no cloud dependency.

The system implements **three novel research contributions**:

| Contribution | Description |
|---|---|
| 🔴 **Novel #1** - Resource-Adaptive Task Decomposition (RAD) | Monitors live RAM/CPU before each plan step. Automatically splits steps into sub-tasks when memory is constrained |
| 🔴 **Novel #2** - Sensor Failure Recovery Protocol | Formal 4-type failure taxonomy (TIMEOUT, BAD\_DATA, DISCONNECT, BUS\_ERROR) with criticality-based recovery action matrix |
| 🔴 **Novel #3** - AgentEdge-Bench | First standardized 20-task benchmark suite for embedded agentic AI systems |

**Target Publication:** IEEE Internet of Things Journal / IEEE IOTAIS 2026

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Raspberry Pi 5 (8GB)                  │
│                                                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────┐  │
│  │ PERCEIVE │───▶│  PLAN    │───▶│    EXECUTE        │  │
│  │ 5 Sensors│    │  SLM     │    │ Resource-Adaptive │  │
│  └──────────┘    │ Planner  │    │ Decomposition     │  │
│       ▲          └──────────┘    └──────────────────┘  │
│       │                                   │             │
│  ┌────┴─────┐                    ┌────────▼──────────┐  │
│  │ RECOVER  │◀───────────────────│     VERIFY        │  │
│  │ Protocol │                    │  Sensor Re-read   │  │
│  └──────────┘                    └───────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │                    │
    ┌────▼────┐          ┌────▼────┐
    │ 5 Sensors│         │6 Actuators│
    │ MPU6050  │         │ Relay    │
    │ DHT22    │         │ Buzzer   │
    │ INA219   │         │ LEDs ×3  │
    │ HC-SR04  │         │ BLE/ESP32│
    │ MQ-135   │         └─────────┘
    └──────────┘
```

---

## Research Context

### Problem Statement
> *"Can a quantized Small Language Model running on a Raspberry Pi 5 reliably execute multi-step agentic tasks using real physical sensors - with graceful recovery from sensor failure - without any cloud connectivity?"*

### Research Gap
All existing agentic AI frameworks (LangGraph, AutoGen, ReAct) are designed for cloud deployment with unlimited RAM and compute. No published paper has:
- Deployed a complete agentic loop on embedded hardware with real sensor tool-use
- Defined a formal sensor failure recovery protocol for embedded agents
- Created a standardized benchmark for embedded agentic AI

### Novel Contributions
1. **Resource-Adaptive Task Decomposition** - First algorithm that monitors live device RAM/CPU and splits agent plan steps at runtime to prevent OOM failures
2. **Embedded Agent Recovery Protocol** - Formal failure taxonomy + criticality-based action matrix (RETRY / SUBSTITUTE / SKIP / ABORT)
3. **AgentEdge-Bench** - 20-task benchmark across 4 categories (Normal, Single Fault, Multi-Fault, Recovery) with ground-truth metrics

---

## Project Structure

```
AgentEdge/
├── main.py                      # Entry point: demo | loop N | bench
├── requirements.txt
│
├── config/
│   └── settings.py              # Central configuration (LLM, thresholds, modes)
│
├── core/
│   ├── logger.py                # Structured logging + JSON event log
│   └── resource_monitor.py      # ⭐ Novel #1: RAM/CPU monitor + state machine
│
├── sensors/
│   ├── simulator.py             # Physics-based 5-sensor simulator (Phase 1)
│   └── hardware_reader.py       # Real GPIO/I2C reader (Phase 2: RPi5)
│
├── actuators/
│   ├── simulator.py             # 6-actuator simulator with machine feedback
│   └── hardware_writer.py       # Real GPIO writer (Phase 2: RPi5)
│
├── agent/
│   ├── recovery.py              # ⭐ Novel #2: Failure taxonomy + recovery matrix
│   ├── planner.py               # LLM planning brain (Gemini/Claude/Ollama)
│   ├── executor.py              # Plan runner with resource-adaptive splitting
│   └── loop.py                  # Full Perceive→Plan→Execute→Verify→Recover
│
├── benchmark/
│   └── runner.py                # ⭐ Novel #3: 20-task AgentEdge-Bench suite
│
├── tests/
│   └── test_all.py              # 34 unit tests (all passing)
│
├── docs/
│   └── SETUP_GUIDE.md           # Full setup and hardware transition guide
│
└── logs/                        # Session reports and event logs (gitignored)
```

---

## Quick Start

### Prerequisites
- Python 3.10+
- A Gemini API key (free at [aistudio.google.com](https://aistudio.google.com))

### Installation

```bash
# Clone the repository
git clone https://github.com/nikhhiilsingghvi/agentedge.git
cd agentedge

# Install dependencies
pip install google-genai psutil rich pydantic httpx anthropic

# Set your API key
# Windows PowerShell:
$env:GEMINI_API_KEY = "AIza-your-key-here"
# Mac/Linux:
export GEMINI_API_KEY="AIza-your-key-here"
```

### Run

```bash
# Quick 3-cycle demo
python main.py demo

# Run 10 cycles
python main.py loop 10

# Run full 20-task benchmark
python main.py bench

# Run unit tests
python -m pytest tests/ -v
```

---

## Phase 1 Results (Simulation on Laptop)

| Metric | Value |
|---|---|
| Avg Plan Latency | 1653 ms (Gemini) / 7272 ms (Claude) |
| Avg Tokens/Plan | 935 |
| Peak RAM | 83.2% (laptop) |
| Avg Power | 4.89 W |
| Recovery Success Rate | 50–100% (by failure type) |
| Steps Split (RAD) | 0 (laptop RAM sufficient) |
| Parse Errors | 0 |
| Task Completion | 100% |

*Note: On Raspberry Pi 5 8GB, RAM will be ~45–55% and RAD algorithm activations will be measurable.*

---

## Sensor Specifications

| Sensor | Component | Normal | Warning | Fault | Unit |
|---|---|---|---|---|---|
| mpu6050 | 6-axis IMU | < 1.5 | 1.5–3.0 | ≥ 3.0 | g |
| dht22 | Temperature | < 45 | 45–60 | ≥ 60 | °C |
| ina219 | Current | < 1.5 | 1.5–2.0 | ≥ 2.0 | A |
| hcsr04 | Ultrasonic | > 10 | — | < 10 | cm |
| mq135 | Gas/Fumes | < 200 | 200–400 | ≥ 400 | ppm |

---

## Configuration

All settings in `config/settings.py`:

```python
# LLM Backend
LLM_PROVIDER = "gemini"              # gemini | anthropic | ollama
LLM_MODEL    = "gemini-3.1-flash-lite"  # or claude-sonnet-4-6 or tinyllama

# Resource Thresholds (Novel Contribution #1)
RAM_WARN_THRESHOLD_PERCENT     = 85.0   # Warn when RAM > 85%
RAM_CRITICAL_THRESHOLD_PERCENT = 85.0   # Split tasks when RAM > 85%

# Simulation
SIMULATION_MODE = True    # False for real RPi5 hardware
```

---

## Phase Roadmap

### Phase 1 - Simulation (Current)
- All 5 sensors simulated with physics-based fault injection
- LLM planning via Gemini API (free tier)
- 34 unit tests passing
- 20-task benchmark suite implemented

### Phase 2 - Hardware Integration (Oct 2026)
- Replace `sensors/simulator.py` → `sensors/hardware_reader.py`
- Replace `actuators/simulator.py` → `actuators/hardware_writer.py`
- Switch `LLM_PROVIDER = "ollama"` + `ollama pull tinyllama`
- Set `SIMULATION_MODE = False`
- Everything else unchanged

### Phase 3 - Research Paper (Nov 2026)
- Full benchmark results on RPi5
- Latency / RAM / Energy characterization
- Ablation study: TinyLlama vs Phi-3-Mini
- IEEE conference submission

---

## Team

| Member | Role | Responsibilities |
|---|---|---|
| Member 1 | LLM Engineer + Project Lead | `main.py`, `config/`, `agent/planner.py`, `agent/loop.py` |
| Member 2 | Sensor Engineer | `sensors/`, sensor thresholds |
| Member 3 | Actuator + Recovery Engineer | `actuators/`, `agent/recovery.py`, `agent/executor.py` |
| Member 4 | Benchmark + Testing | `benchmark/`, `tests/` |

**Institution:** Department of Electronics & Communication Engineering, Dayananda Sagar College of Engineering, Bengaluru

---

## References

1. Shen et al., *"Large Language Models Empowered Autonomous Edge AI"*, **IEEE Commun. Mag.**, 2024
2. Yao et al., *"ReAct: Synergizing Reasoning and Acting in LLMs"*, **ICLR**, 2023
3. Navardi et al., *"Metareasoning for Edge-Cloud Collaborative LLM Planning"*, **ACM TECS**, 2025
4. Song et al., *"LLM-Planner: Few-Shot Grounded Planning for Embodied Agents"*, **IEEE/CVF ICCV**, 2023
5. Rüb & Gerhards, *"Toward a Modular Architecture for Embedded AI Agent Systems"*, arXiv:2606.02862, 2025

---

## Disclaimer

This is a research prototype developed as a final-year major project. The patent-pending algorithms (Resource-Adaptive Task Decomposition and Sensor Failure Recovery Protocol) are novel contributions intended for IEEE publication.

---

## License

Copyright (c) 2026 AgentEdge Project - DSCE ECE Department, Bengaluru

This project is licensed under the MIT License - see [LICENSE](LICENSE) for details.
