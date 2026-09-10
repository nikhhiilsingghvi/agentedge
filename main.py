import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.logger import get_logger, console
from agent.loop import AgentLoop
# pyrefly: ignore [missing-import]
from benchmark.runner import BenchmarkRunner

logger = get_logger("Main")

def run_demo():
    console.print("\n[bold cyan]AgentEdge — Phase 1 Simulation Demo[/bold cyan]")
    console.print("[dim]Running 3 cycles to verify system...[/dim]\n")
    loop = AgentLoop()
    loop.run(max_cycles=3)
    console.print("\n[bold green]✅ Demo complete![/bold green]")

def run_loop(max_cycles=None):
    console.print(f"\n[bold cyan]AgentEdge — Agent Loop[/bold cyan]")
    if max_cycles:
        console.print(f"[dim]Running {max_cycles} cycles...[/dim]\n")
    else:
        console.print("[dim]Running continuously (Ctrl+C to stop)...[/dim]\n")
    loop = AgentLoop()
    loop.run(max_cycles=max_cycles)

def run_benchmark():
    console.print("\n[bold cyan]AgentEdge-Bench — 20-Task Benchmark[/bold cyan]")
    console.print("[dim]This will take several minutes...[/dim]\n")
    loop = AgentLoop()
    bench = BenchmarkRunner(loop)
    report = bench.run_all()
    console.print(f"\n[bold green]✅ Benchmark complete![/bold green]")
    console.print(f"Completion Rate: [yellow]{report['completion_rate_pct']}%[/yellow]")
    console.print(f"Avg Score:       [yellow]{report['avg_composite_score']}[/yellow]")
    console.print(f"Avg Latency:     [yellow]{report['avg_plan_latency_ms']:.0f} ms[/yellow]")

def main():
    args = sys.argv[1:]
    if not args or args[0] == "demo":
        run_demo()
    elif args[0] == "loop":
        cycles = int(args[1]) if len(args) > 1 else None
        run_loop(max_cycles=cycles)
    elif args[0] == "bench":
        run_benchmark()
    else:
        print("Usage: python main.py [demo|loop [N]|bench]")
        sys.exit(1)

if __name__ == "__main__":
    main()
