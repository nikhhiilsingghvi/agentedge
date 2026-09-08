from dataclasses import dataclass, field
from typing import Optional
import os

SIMULATION_MODE: bool = True   
LLM_PROVIDER: str = "gemini"
LLM_MODEL: str = "gemini-3.1-flash-lite"
LLM_MAX_TOKENS: int = 1024
LLM_TEMPERATURE: float = 0.1            

OLLAMA_BASE_URL: str = "http://localhost:11434"
OLLAMA_MODEL: str = "tinyllama"

RAM_WARN_THRESHOLD_PERCENT: float = 70.0   
RAM_CRITICAL_THRESHOLD_PERCENT: float = 85.0  
CPU_WARN_THRESHOLD_PERCENT: float = 80.0
MAX_PLAN_STEPS: int = 10                  

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

SIM_SENSOR_UPDATE_INTERVAL: float = 2.0  
SIM_FAULT_PROBABILITY: float = 0.15       
SIM_SENSOR_FAILURE_PROB: float = 0.05    
AGENT_LOOP_INTERVAL: float = 3.0         
MAX_RECOVERY_ATTEMPTS: int = 3           
RECOVERY_WAIT_SECONDS: float = 1.0      

BENCHMARK_TASKS_FILE: str = "benchmark/tasks.json"
BENCHMARK_RESULTS_DIR: str = "logs/benchmark_results"

LOG_DIR: str = "logs"
LOG_LEVEL: str = "DEBUG"
LOG_TO_FILE: bool = True
LOG_ROTATION_MB: int = 10

SIM_ACTUATOR_LATENCY_MS: float = 50.0   
SIM_IDLE_POWER_WATTS: float = 4.5       
SIM_INFERENCE_POWER_WATTS: float = 7.2  
SIM_GPIO_POWER_WATTS: float = 0.1       

PROJECT_NAME: str = "AgentEdge"
PROJECT_VERSION: str = "1.0.0-simulation"
INSTITUTION: str = "DSCE ECE Dept, Bengaluru"
HARDWARE_TARGET: str = "Raspberry Pi 5 8GB"
