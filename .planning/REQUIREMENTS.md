# REQUIREMENTS: AgentEdge

## v1 Requirements (In Scope)

### Sensor Pipeline
- REQ-001: Read all 5 sensors (MPU6050, DHT22, INA219, HC-SR04, MQ-135) every cycle
- REQ-002: Classify each reading as NORMAL / WARNING / FAULT / FAILED
- REQ-003: Simulate all sensors with physics-based fault injection (Phase 1)
- REQ-004: Read from real GPIO/I2C hardware with identical interface (Phase 2)

### LLM Planning Engine
- REQ-005: Generate structured JSON action plans from sensor readings
- REQ-006: Support Gemini API backend (Phase 1)
- REQ-007: Support TinyLlama via Ollama backend (Phase 2)
- REQ-008: Parse LLM output with 5-strategy robust JSON parser
- REQ-009: Fall back to safe state plan on parse failure

### Actuator Control
- REQ-010: Execute plans across 6 actuators (Relay, Buzzer, 3 LEDs, BLE)
- REQ-011: Simulate actuator responses with machine state feedback (Phase 1)
- REQ-012: Write to real GPIO hardware (Phase 2)

### Sensor Failure Recovery (Novel #2)
- REQ-013: Detect 4 failure types: TIMEOUT, BAD_DATA, DISCONNECT, BUS_ERROR
- REQ-014: Apply criticality-based recovery actions: RETRY / SUBSTITUTE / SKIP / ABORT
- REQ-015: Log all recovery attempts and outcomes

### Resource-Adaptive Task Decomposition (Novel #1)
- REQ-016: Monitor RAM and CPU before every plan step execution
- REQ-017: Classify resource state as NORMAL / WARN / CRITICAL
- REQ-018: Automatically split plan steps when CRITICAL threshold exceeded
- REQ-019: Log all resource snapshots for benchmark analysis

### Benchmark Suite (Novel #3)
- REQ-020: 20 standardized tasks across 4 categories (Normal, Single Fault, Multi-Fault, Recovery)
- REQ-021: Score each task on: latency, step correctness, resource usage, recovery success
- REQ-022: Generate structured JSON benchmark report

### Hardware Transition (Phase 2)
- REQ-023: hardware_reader.py drop-in replacement for simulator.py (identical interface)
- REQ-024: hardware_writer.py drop-in replacement for actuator simulator
- REQ-025: Full offline operation on RPi5 with TinyLlama

### Research Paper
- REQ-026: Collect and export latency, token count, RAM, CPU, and energy metrics per cycle
- REQ-027: Ablation: compare TinyLlama vs Gemini on AgentEdge-Bench

## v2 Requirements (Future)
- REQ-V2-001: Web dashboard for live monitoring
- REQ-V2-002: BLE real phone alert integration
- REQ-V2-003: Phi-3-Mini model comparison

## Out of Scope
- Cloud connectivity or remote inference
- Support for hardware other than RPi5
- Multi-machine or distributed monitoring
