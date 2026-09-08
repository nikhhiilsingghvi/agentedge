# AgentEdge — Contributors

## Project Team

**Institution:** Department of Electronics & Communication Engineering  
**College:** Dayananda Sagar College of Engineering, Bengaluru — 560078  
**Course:** Final Year Major Project (21ECP76)  
**Academic Year:** 2025–2026  

---

## Team Members

| Member | Role | Files Owned |
|---|---|---|
| **Nikhil** (Member 1) | Project Lead + LLM Engineer | `main.py`, `config/settings.py`, `agent/planner.py`, `agent/loop.py` |
| **Member 2** | Sensor & Simulation Engineer | `sensors/simulator.py`, `sensors/hardware_reader.py` |
| **Member 3** | Actuator & Recovery Engineer | `actuators/simulator.py`, `actuators/hardware_writer.py`, `agent/recovery.py`, `agent/executor.py` |
| **Member 4** | Benchmark & Testing Engineer | `benchmark/runner.py`, `tests/test_all.py` |

---

## Shared Modules

The following modules are maintained collectively:

- `core/logger.py` — Structured logging system
- `core/resource_monitor.py` — Resource monitoring (Novel Contribution #1)
- `docs/SETUP_GUIDE.md` — Setup documentation

---

## Guide / Supervisor

Department of Electronics & Communication Engineering  
Dayananda Sagar College of Engineering

---

## Acknowledgements

This project builds on the following foundational works:

- Yao et al. (2023) — ReAct agentic reasoning paradigm (ICLR 2023)
- Shen et al. (2024) — LLM-empowered edge AI (IEEE Communications Magazine)
- Navardi et al. (2025) — Edge-cloud LLM planning (ACM TECS)
- Rüb & Gerhards (2025) — Modular embedded agent architecture (arXiv)

---

## Research Target

**IEEE Internet of Things Journal** / **IEEE IOTAIS 2026**

Novel contributions:
1. Resource-Adaptive Task Decomposition algorithm
2. Embedded Sensor Failure Recovery Protocol
3. AgentEdge-Bench: 20-task embedded agent benchmark
