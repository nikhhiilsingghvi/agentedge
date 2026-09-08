# ROADMAP: AgentEdge

**5 phases | 27 v1 requirements mapped | 100% coverage**

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|-----------------|
| 1 | Simulation Foundation | Complete and stabilize Phase 1 simulation | REQ-001 to REQ-011 | 3 |
| 2 | Novel Algorithms | Implement and verify RAD + Recovery Protocol | REQ-013 to REQ-019 | 3 |
| 3 | Benchmark Suite | Build and run AgentEdge-Bench 20-task suite | REQ-020 to REQ-022 | 3 |
| 4 | Hardware Transition | Deploy to real RPi5 with real sensors/actuators | REQ-004, REQ-012, REQ-023 to REQ-025 | 4 |
| 5 | Research Paper | Collect data, run ablations, write IEEE paper | REQ-026, REQ-027 | 3 |

---

### Phase 1: Simulation Foundation
**Goal:** Stable, tested, fully-integrated simulation loop
**Mode:** standard
**Requirements:** REQ-001, REQ-002, REQ-003, REQ-005 to REQ-011
**Status:** COMPLETE
**Success Criteria:**
1. python main.py demo runs 3 cycles with zero parse errors
2. All 34 unit tests pass (pytest tests/ -v)
3. Avg plan latency < 3000ms on Gemini API

---

### Phase 2: Novel Algorithms
**Goal:** Implement, test and measure RAD and Sensor Recovery Protocol
**Mode:** thorough
**Requirements:** REQ-013, REQ-014, REQ-015, REQ-016, REQ-017, REQ-018, REQ-019
**Status:** IN PROGRESS
**Success Criteria:**
1. RAD triggers correctly when RAM > 85% - step splitting observed in logs
2. All 4 sensor failure types handled with appropriate recovery action
3. Recovery success rate >= 50% logged per failure type

---

### Phase 3: Benchmark Suite
**Goal:** Run full 20-task AgentEdge-Bench and generate results for paper
**Mode:** thorough
**Requirements:** REQ-020, REQ-021, REQ-022
**Status:** COMPLETE (Phase 1 results available)
**Success Criteria:**
1. All 20 benchmark tasks complete without crash
2. Structured JSON report generated with all metrics
3. Results cover: latency, token count, RAM, energy, recovery rate

---

### Phase 4: Hardware Transition
**Goal:** Deploy AgentEdge on real Raspberry Pi 5 with physical sensors
**Mode:** thorough
**Requirements:** REQ-004, REQ-012, REQ-023, REQ-024, REQ-025
**Status:** PLANNED (Oct 2026)
**Success Criteria:**
1. All 5 real sensors read correctly via hardware_reader.py
2. All 6 actuators respond via hardware_writer.py
3. TinyLlama runs offline on RPi5 with < 10s inference latency
4. Full 20-task benchmark runs on real hardware

---

### Phase 5: Research Paper
**Goal:** Write and submit IEEE paper with experimental results
**Mode:** thorough
**Requirements:** REQ-026, REQ-027
**Status:** PLANNED (Nov 2026)
**Success Criteria:**
1. Full metrics dataset from both Gemini and TinyLlama runs exported
2. Ablation study (TinyLlama vs Gemini) complete on AgentEdge-Bench
3. Paper submitted to IEEE IoT Journal / IEEE IOTAIS 2026
