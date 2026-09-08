from anthropic.types.beta import beta_request_mcp_server_tool_configuration_param
import json
import re
import time
import os
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from google import genai as google_genai
    import os
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from config.settings import (
    LLM_PROVIDER, LLM_MODEL, LLM_MAX_TOKENS,
    OLLAMA_BASE_URL, OLLAMA_MODEL, SIMULATION_MODE,
)
from core.logger import get_logger

logger = get_logger("LLMPlanner")

@dataclass
class PlanStep:
    step_id:      str
    description:  str
    action_type:  str
    target:       str
    command:      str
    payload:      Optional[Dict] = None
    can_split:    bool = True

    def to_dict(self) -> dict:
        return {
            "step_id":     self.step_id,
            "description": self.description,
            "action_type": self.action_type,
            "target":      self.target,
            "command":     self.command,
            "payload":     self.payload,
            "can_split":   self.can_split,
        }

@dataclass
class AgentPlan:
    plan_id:       str
    situation:     str
    severity:      str
    steps:         List[PlanStep]
    reasoning:     str
    latency_ms:    float
    model_used:    str
    token_count:   int

    def to_dict(self) -> dict:
        return {
            "plan_id":     self.plan_id,
            "situation":   self.situation,
            "severity":    self.severity,
            "steps":       [s.to_dict() for s in self.steps],
            "reasoning":   self.reasoning,
            "latency_ms":  round(self.latency_ms, 2),
            "model_used":  self.model_used,
            "token_count": self.token_count,
        }

SYSTEM_PROMPT = """You are AgentEdge, an embedded industrial AI agent running on a Raspberry Pi 5.
You monitor a factory machine using 5 sensors and control 6 actuators.

SENSORS:
- mpu6050: Vibration (g-force). Normal < 1.5g. Warning 1.5-3.0g. FAULT >= 3.0g.
- dht22: Temperature (Celsius). Normal < 45C. Warning 45-60C. FAULT >= 60C.
- ina219: Motor current (Amps). Normal < 1.5A. Warning 1.5-2.0A. FAULT >= 2.0A.
- hcsr04: Distance (cm). JAM if object < 10cm.
- mq135: Gas/fumes (ppm). Normal < 200. Warning 200-400. FAULT >= 400.

ACTUATORS:
- relay: Motor power control. Commands: ON, OFF.
- buzzer: Audible alarm. Commands: ON, OFF.
- led_red: Critical fault LED. Commands: ON, OFF.
- led_grn: Normal status LED. Commands: ON, OFF.
- led_yel: Warning LED. Commands: ON, OFF.
- ble: BLE wireless alert to operator phone. Command: ALERT with message.

YOUR TASK:
Analyze sensor readings and produce a structured JSON action plan.

RULES:
1. If FAULT detected on mpu6050 or ina219, ALWAYS shut down motor (relay OFF).
2. Always send BLE alert for WARNING or FAULT conditions.
3. Keep steps minimal and specific.
4. Return ONLY valid JSON. No markdown. No explanation outside the JSON.

OUTPUT FORMAT (return ONLY this JSON, nothing else):
{
  "situation": "one sentence summary",
  "severity": "NORMAL",
  "reasoning": "2-3 sentence explanation",
  "steps": [
    {
      "step_id": "S1",
      "description": "what this step does",
      "action_type": "ACTUATOR",
      "target": "led_grn",
      "command": "ON",
      "payload": null
    }
  ]
}

severity must be exactly one of: NORMAL, WARNING, CRITICAL, FAULT
action_type must be exactly one of: ACTUATOR, ALERT, LOG, VERIFY, WAIT
Keep steps array short (1-4 steps max). Do not truncate the JSON."""

def format_perception(sensor_readings: dict) -> str:
    lines = ["CURRENT SENSOR READINGS:"]
    for sid, reading in sensor_readings.items():
        if reading.is_failed:
            lines.append(f"  {sid.upper()}: SENSOR FAILED ({reading.failure_reason})")
        else:
            status_tag = f"[{reading.status.value}]"
            lines.append(
                f"  {sid.upper()}: {reading.value} {reading.unit} {status_tag}"
            )
    return "\n".join(lines)

def _extract_json(raw: str) -> Optional[dict]:
    clean = raw.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*\n?", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\n?```\s*$", "", clean).strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    fixed = re.sub(r",(\s*[}\]])", r"\1", clean)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{.*\}', fixed, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    steps_match = re.search(r'"steps"\s*:\s*\[', fixed)
    if steps_match:
        try:
            steps_start = steps_match.end()
            depth = 0
            in_string = False
            escape_next = False
            step_ends = []
            i = steps_start
            while i < len(fixed):
                ch = fixed[i]
                if escape_next:
                    escape_next = False
                elif ch == '\\' and in_string:
                    escape_next = True
                elif ch == '"' and not escape_next:
                    in_string = not in_string
                elif not in_string:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            step_ends.append(i)
                i += 1

            if step_ends:
                recovered = fixed[:steps_start] + fixed[steps_start:step_ends[-1]+1] + "\n  ]\n}"
                recovered = re.sub(r",(\s*[}\]])", r"\1", recovered)
                return json.loads(recovered)
        except Exception:
            pass

    return None

def parse_plan(raw_json: str, plan_id: str, latency_ms: float, model: str, tokens: int) -> AgentPlan:
    data = _extract_json(raw_json)

    if data is None:
        logger.error("[planner] All parse strategies failed. Raw (first 300 chars): %s",
                     raw_json[:300])
        return _safe_default_plan(plan_id, latency_ms, model, tokens)

    steps = []
    for i, s in enumerate(data.get("steps", [])):
        steps.append(PlanStep(
            step_id=s.get("step_id", f"S{i+1}"),
            description=s.get("description", ""),
            action_type=s.get("action_type", "ACTUATOR"),
            target=s.get("target", ""),
            command=s.get("command", ""),
            payload=s.get("payload"),
        ))

    return AgentPlan(
        plan_id=plan_id,
        situation=data.get("situation", "Unknown"),
        severity=data.get("severity", "NORMAL"),
        steps=steps,
        reasoning=data.get("reasoning", ""),
        latency_ms=latency_ms,
        model_used=model,
        token_count=tokens,
    )

def _safe_default_plan(plan_id, latency_ms, model, tokens) -> AgentPlan:
    return AgentPlan(
        plan_id=plan_id,
        situation="Planner output could not be parsed — safe state activated",
        severity="CRITICAL",
        steps=[
            PlanStep("S1", "Sound alarm", "ACTUATOR", "buzzer", "ON"),
            PlanStep("S2", "BLE alert", "ALERT", "ble", "ALERT",
                     payload={"message": "Agent planner error — manual inspection required",
                              "severity": "CRITICAL"}),
        ],
        reasoning="Parse failure — defaulting to safe state protocol.",
        latency_ms=latency_ms,
        model_used=model,
        token_count=tokens,
    )

class LLMPlanner:
    def __init__(self):
        self._call_count = 0
        self._total_latency_ms = 0.0
        self._total_tokens = 0

        if LLM_PROVIDER == "anthropic" and ANTHROPIC_AVAILABLE:
            self._client = anthropic.Anthropic()
            self._backend = "anthropic"
            logger.info("[planner] Backend: Anthropic (%s) — simulation mode", LLM_MODEL)

        elif LLM_PROVIDER == "gemini" and GEMINI_AVAILABLE:
            self._client = google_genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
            self._backend = "gemini"
            logger.info("[planner] Backend: Gemini (%s) — simulation mode", LLM_MODEL)

        elif LLM_PROVIDER == "ollama" and HTTPX_AVAILABLE:
            self._client = None
            self._backend = "ollama"
            logger.info("[planner] Backend: Ollama (%s) — hardware mode", OLLAMA_MODEL)

        else:
            raise RuntimeError(
                "No LLM backend available. Install 'google-generativeai' for Gemini, "
                "'anthropic' for Claude, or ensure Ollama is running for hardware mode."
            )

    def plan(self, sensor_readings: dict, context: Optional[str] = None) -> AgentPlan:
        self._call_count += 1
        plan_id = f"PLAN_{self._call_count:04d}_{int(time.time())}"

        perception_text = format_perception(sensor_readings)
        user_message = perception_text
        if context:
            user_message += f"\n\nCONTEXT: {context}"

        logger.info("[planner] 🧠 Generating plan %s ...", plan_id)
        logger.debug("[planner] Perception:\n%s", perception_text)

        t0 = time.time()

        try:
            if self._backend == "anthropic":
                raw, tokens = self._call_anthropic(user_message)
            elif self._backend == "gemini":
                raw, tokens = self._call_gemini(user_message)
            elif self._backend == "ollama":
                raw, tokens = self._call_ollama(user_message)
            else:
                raise RuntimeError(f"Unsupported backend: {self._backend}")

            latency_ms = (time.time() - t0) * 1000
            self._total_latency_ms += latency_ms
            self._total_tokens += tokens

            plan = parse_plan(raw, plan_id, latency_ms, LLM_MODEL, tokens)

            logger.info(
                "[planner] ✅ Plan ready: %s | Severity: %s | Steps: %d | "
                "Latency: %.0f ms | Tokens: %d",
                plan_id, plan.severity, len(plan.steps), latency_ms, tokens,
            )
            logger.info("[planner] Situation: %s", plan.situation)
            return plan

        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            logger.error("[planner] ❌ Plan generation failed: %s", e)
            return _safe_default_plan(plan_id, latency_ms, LLM_MODEL, 0)

    def _call_anthropic(self, user_message: str):
        response = self._client.messages.create(
            model=LLM_MODEL,
            max_tokens=LLM_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text
        tokens = response.usage.input_tokens + response.usage.output_tokens
        return text, tokens
    
    def _call_gemini(self, user_message: str):
        response = self._client.models.generate_content(
            model=LLM_MODEL,
            contents=user_message,
            config={
                "system_instruction": SYSTEM_PROMPT,
                "max_output_tokens": LLM_MAX_TOKENS,
                "temperature": 0.1,
            },
        )
        text = response.text
        tokens = (
            response.usage_metadata.total_token_count
        if response.usage_metadata else 0
        )
        return text, tokens

    def _call_ollama(self, user_message: str):
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {"num_predict": LLM_MAX_TOKENS},
        }
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = data["message"]["content"]
        tokens = data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
        return text, tokens

    def get_statistics(self) -> dict:
        return {
            "total_plans_generated": self._call_count,
            "avg_latency_ms": round(self._total_latency_ms / max(self._call_count, 1), 2),
            "total_tokens_used": self._total_tokens,
            "avg_tokens_per_plan": round(self._total_tokens / max(self._call_count, 1), 1),
            "backend": self._backend,
            "model": LLM_MODEL if self._backend in ("anthropic", "gemini") else OLLAMA_MODEL,
        }
