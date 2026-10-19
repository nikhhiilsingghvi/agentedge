"""
main.py — AgentEdge entry point
================================
Usage:
  python main.py demo [--mode simulation|hybrid]   # 3-cycle demo
  python main.py loop [N] [--mode simulation|hybrid]  # run N (or infinite) cycles
  python main.py bench [--mode simulation]         # 20-task benchmark
  python main.py hardware-check                    # diagnose I2C and GPIO hardware

--mode sets AGENT_MODE before any module is imported.
Default mode: simulation (safe everywhere, no hardware required).
"""
import os
import sys

# ── Parse --mode early, before any project module is imported ─────────────────
_VALID_MODES = ("simulation", "hybrid", "hardware")
_mode = "simulation"
_args = sys.argv[1:]
if "--mode" in _args:
    idx = _args.index("--mode")
    if idx + 1 < len(_args):
        _mode = _args[idx + 1].lower()
        _args = [a for a in _args if a not in ("--mode", _mode)]
    else:
        print("Error: --mode requires an argument (simulation | hybrid | hardware)")
        sys.exit(1)

if _mode not in _VALID_MODES:
    print(f"Error: unknown mode {_mode!r}. Choose from: {_VALID_MODES}")
    sys.exit(1)

os.environ.setdefault("AGENT_MODE", _mode)   # settings.py reads this

# ── Normal imports now ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env (GEMINI_API_KEY, etc.) before any module initialises a client
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

from core.logger import get_logger, console
from agent.loop import AgentLoop
from benchmark.runner import BenchmarkRunner

logger = get_logger("Main")


# ── Command implementations ───────────────────────────────────────────────────

def run_demo(mode: str) -> None:
    console.print(f"\n[bold cyan]AgentEdge — Demo  mode={mode.upper()}[/bold cyan]")
    console.print("[dim]Running 3 cycles to verify system...[/dim]\n")
    loop = AgentLoop()
    loop.run(max_cycles=3)
    console.print("\n[bold green]✅ Demo complete![/bold green]")


def run_loop(max_cycles=None, mode: str = "simulation") -> None:
    console.print(f"\n[bold cyan]AgentEdge — Agent Loop  mode={mode.upper()}[/bold cyan]")
    if max_cycles:
        console.print(f"[dim]Running {max_cycles} cycles...[/dim]\n")
    else:
        console.print("[dim]Running continuously (Ctrl+C to stop)...[/dim]\n")
    loop = AgentLoop()
    loop.run(max_cycles=max_cycles)


def run_benchmark() -> None:
    console.print("\n[bold cyan]AgentEdge-Bench — 20-Task Benchmark[/bold cyan]")
    console.print("[dim]This will take several minutes...[/dim]\n")
    loop = AgentLoop()
    bench = BenchmarkRunner(loop)
    report = bench.run_all()
    console.print("\n[bold green]✅ Benchmark complete![/bold green]")
    console.print(f"Completion Rate: [yellow]{report['completion_rate_pct']}%[/yellow]")
    console.print(f"Avg Score:       [yellow]{report['avg_composite_score']}[/yellow]")
    console.print(f"Avg Latency:     [yellow]{report['avg_plan_latency_ms']:.0f} ms[/yellow]")


def run_hardware_check() -> None:
    """
    Diagnose attached hardware without starting the full agent loop.
    Performs actual I2C and GPIO probes — not a simulated check.
    """
    import importlib

    console.print("\n[bold cyan]AgentEdge — Hardware Check[/bold cyan]")
    console.print("[dim]Probing I2C bus and GPIO — requires Raspberry Pi 4[/dim]\n")

    all_ok = True

    # ── 1. smbus2 ─────────────────────────────────────────────────────────────
    try:
        import smbus2
        console.print("[green]✅  smbus2 installed[/green]")
    except ImportError:
        console.print("[red]❌  smbus2 not installed — run: pip install smbus2[/red]")
        all_ok = False

    # ── 2. RPi.GPIO ──────────────────────────────────────────────────────────
    try:
        import RPi.GPIO as GPIO
        console.print("[green]✅  RPi.GPIO installed[/green]")
    except ImportError:
        console.print("[red]❌  RPi.GPIO not installed — run: pip install RPi.GPIO[/red]")
        all_ok = False

    # ── 3. I2C bus available ──────────────────────────────────────────────────
    try:
        import smbus2
        bus = smbus2.SMBus(1)
        bus.close()
        console.print("[green]✅  I2C bus 1 opened successfully[/green]")
    except FileNotFoundError:
        console.print("[red]❌  I2C bus 1 not found — enable I2C: sudo raspi-config → Interface Options → I2C[/red]")
        all_ok = False
    except Exception as exc:
        console.print(f"[red]❌  I2C bus error: {exc}[/red]")
        all_ok = False

    # ── 4. MPU6050 responds at 0x68 ──────────────────────────────────────────
    try:
        import smbus2
        bus = smbus2.SMBus(1)
        who = bus.read_byte_data(0x68, 0x75)   # WHO_AM_I register
        bus.close()
        if who == 0x68:
            console.print(f"[green]✅  MPU6050 found at 0x68 (WHO_AM_I=0x{who:02X})[/green]")
        else:
            console.print(f"[yellow]⚠️   Device at 0x68 returned WHO_AM_I=0x{who:02X} (expected 0x68)[/yellow]")
    except Exception as exc:
        console.print(f"[red]❌  MPU6050 not responding at 0x68: {exc}[/red]")
        console.print("[dim]   Check wiring: SDA→GPIO2(pin 3), SCL→GPIO3(pin 5), VCC→3.3V, GND→GND[/dim]")
        all_ok = False

    # ── 5. MPU6050 live data ──────────────────────────────────────────────────
    try:
        from sensors.hardware_reader import _mpu6050
        _mpu6050._initialized = False   # force re-init for fresh check
        val, raw, error = _mpu6050.read_vibration()
        if error:
            console.print(f"[red]❌  MPU6050 read failed: {error}[/red]")
            all_ok = False
        else:
            console.print(
                f"[green]✅  MPU6050 live data: "
                f"ax={raw.get('ax_g'):.3f}g  "
                f"ay={raw.get('ay_g'):.3f}g  "
                f"az={raw.get('az_g'):.3f}g  "
                f"vibration={val:.4f}g[/green]"
            )
    except Exception as exc:
        console.print(f"[red]❌  MPU6050 live read error: {exc}[/red]")
        all_ok = False

    # ── 6. GPIO test (safe: brief LED flash) ─────────────────────────────────
    try:
        import RPi.GPIO as GPIO
        from config.settings import GPIO_PIN_MAP
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        test_pin = GPIO_PIN_MAP.get("led_grn", 25)
        GPIO.setup(test_pin, GPIO.OUT, initial=GPIO.LOW)
        GPIO.output(test_pin, GPIO.HIGH)
        import time; time.sleep(0.2)
        GPIO.output(test_pin, GPIO.LOW)
        GPIO.cleanup()
        console.print(f"[green]✅  GPIO BCM{test_pin} (led_grn) toggled HIGH→LOW successfully[/green]")
    except Exception as exc:
        console.print(f"[red]❌  GPIO test failed: {exc}[/red]")
        all_ok = False

    # ── Summary ───────────────────────────────────────────────────────────────
    if all_ok:
        console.print("\n[bold green]All hardware checks passed — ready for: python main.py demo --mode hybrid[/bold green]")
    else:
        console.print("\n[bold yellow]Some checks failed — review messages above before running hybrid mode[/bold yellow]")


def main() -> None:
    args = _args   # already stripped of --mode / value
    cmd = args[0] if args else "demo"

    if cmd == "demo":
        run_demo(mode=_mode)
    elif cmd == "loop":
        cycles = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        run_loop(max_cycles=cycles, mode=_mode)
    elif cmd in ("bench", "benchmark"):
        run_benchmark()
    elif cmd == "hardware-check":
        run_hardware_check()
    else:
        console.print(
            "[bold]Usage:[/bold]\n"
            "  python main.py demo [--mode simulation|hybrid]\n"
            "  python main.py loop [N] [--mode simulation|hybrid]\n"
            "  python main.py bench\n"
            "  python main.py hardware-check"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
