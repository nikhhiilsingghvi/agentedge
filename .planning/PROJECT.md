# PROJECT: AgentEdge

## Project Summary
AgentEdge is a **research-grade embedded agentic AI system** that runs a Small Language Model (SLM) on a Raspberry Pi 5 to autonomously monitor an industrial factory machine using 5 real sensors, plan multi-step responses to faults, and recover from sensor failures - completely offline with no cloud dependency.

**Project Code:** AGENTEDGE
**Institution:** Dayananda Sagar College of Engineering, ECE Dept., Bengaluru
**Academic Context:** Final Year Major Project (21ECP76), 2025-2026
**Target Publication:** IEEE Internet of Things Journal / IEEE IOTAIS 2026

---

## Type
- **Category:** Embedded AI / Edge AI Research System
- **Stage:** Brownfield (Phase 1 simulation complete, moving to Phase 2 hardware)
- **Workflow Mode:** Thorough

---

## Core Architecture
- **Perceive -> Plan -> Execute -> Verify -> Recover** loop
- **5 Sensors:** MPU6050 (vibration), DHT22 (temp), INA219 (current), HC-SR04 (distance), MQ-135 (gas)
- **6 Actuators:** Relay, Buzzer, LED x3, BLE/ESP32
- **LLM Backend:** Gemini API (Phase 1 sim) / TinyLlama via Ollama (Phase 2 hardware)
- **Target Hardware:** Raspberry Pi 5 8GB

---

## Novel Research Contributions
1. **Novel #1 - Resource-Adaptive Task Decomposition (RAD):** Monitors live RAM/CPU before each plan step; dynamically splits steps into sub-tasks when memory is constrained
2. **Novel #2 - Sensor Failure Recovery Protocol:** Formal 4-type failure taxonomy (TIMEOUT, BAD_DATA, DISCONNECT, BUS_ERROR) with criticality-based recovery action matrix
3. **Novel #3 - AgentEdge-Bench:** First standardized 20-task benchmark suite for embedded agentic AI systems

---

## Team
| Member | Role | Files Owned |
|---|---|---|
| Nikhil (Member 1) | Project Lead + LLM Engineer | main.py, config/settings.py, agent/planner.py, agent/loop.py |
| Member 2 | Sensor Engineer | sensors/simulator.py, sensors/hardware_reader.py |
| Member 3 | Actuator + Recovery Engineer | actuators/, agent/recovery.py, agent/executor.py |
| Member 4 | Benchmark + Testing | benchmark/runner.py, tests/test_all.py |

---

## Current Status (Phase 1 - Complete)
- All 5 sensors simulated with physics-based fault injection
- LLM planning via Gemini API (gemini-3.1-flash-lite)
- 34 unit tests passing
- 20-task benchmark suite implemented
- Agent loop verified: NORMAL to DEGRADING to FAULT to RECOVERY

## Phase 2 - Hardware Integration (Target: Oct 2026)
- Replace sensors/simulator.py with sensors/hardware_reader.py
- Replace actuators/simulator.py with actuators/hardware_writer.py
- Switch LLM_PROVIDER to ollama + pull tinyllama
- Full benchmark on real RPi5 hardware

---

## Key Constraints
- Must run fully offline on RPi5 8GB RAM
- LLM inference must complete within 10 seconds per cycle on hardware
- All 5 sensors must fail gracefully
- JSON output from LLM must parse reliably (5-strategy parser implemented)
