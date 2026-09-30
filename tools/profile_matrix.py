"""Run profile_frame.py cpu for many configurations in interleaved rounds and tabulate them.

    python tools/profile_matrix.py run MATRIX.json --rounds 5 --out DIR
    python tools/profile_matrix.py table DIR

MATRIX.json is a list of configurations:

    [{"name": "helmet-1080-rt", "python": "C:/tmp/prof/w-rt/v/Scripts/python.exe",
      "args": ["helmet", "--frame-info"], "env": {"SHIM_MCCOMPAT": "0x800000001"}}, ...]

Each round runs every configuration once, in a fresh process, in an order rotated by one
position per round, so that drift in GPU clocks and temperature spreads over all
configurations. Before each run the tool samples the CPU load for one second; above
--max-load percent it waits and records the wait. The table reports, per metric, the median of
the per-round medians and the range (minimum to maximum) of the per-round medians.

See docs/how-to/profile.md.
"""

import argparse
import ctypes
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = (
    ("frame_info", "p50", "GPU Filament frame (FrameInfo)"),
    ("gpu_filament_frame", "p50", "GPU start->acquired (host timestamps)"),
    ("gpu_host_draw", "p50", "GPU host draw"),
    ("render", "p50", "render() wall"),
    ("submit", "p50", "native submit"),
    ("acquire", "p50", "acquire() enter"),
    ("draw", "p50", "host draw CPU"),
    ("busy", "p50", "CPU busy"),
    ("busy", "p95", "CPU busy p95"),
    ("interval", "p95", "frame interval p95"),
)


def cpu_load(seconds=1.0):
    """System CPU load in percent over `seconds`, from GetSystemTimes."""
    kernel32 = ctypes.WinDLL("kernel32")
    FILETIME = ctypes.c_ulonglong

    def sample():
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        return idle.value, kernel.value + user.value

    idle0, total0 = sample()
    time.sleep(seconds)
    idle1, total1 = sample()
    total = total1 - total0
    return 100.0 * (1 - (idle1 - idle0) / total) if total else 0.0


def on_ac():
    """True on AC power, False on battery, None if unknown (GetSystemPowerStatus)."""

    class Status(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    status = Status()
    if not ctypes.WinDLL("kernel32").GetSystemPowerStatus(ctypes.byref(status)):
        return None
    return {0: False, 1: True}.get(status.ACLineStatus)


def power_state():
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_Battery).BatteryStatus; powercfg /getactivescheme"],
                             capture_output=True, text=True, timeout=30).stdout.split("\n")
        return {"battery_status": out[0].strip(), "scheme": out[1].strip() if len(out) > 1 else ""}
    except (OSError, subprocess.SubprocessError):
        return {}


def cmd_run(args):
    configs = json.loads(Path(args.matrix).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log = out / "runs.jsonl"
    session = {"start": time.strftime("%Y-%m-%d %H:%M:%S"), "power": power_state(), "ac": on_ac(),
               "rounds": args.rounds, "frames": args.frames, "warmup": args.warmup,
               "configs": configs}
    (out / "session.json").write_text(json.dumps(session, indent=1))
    print("power:", session["power"], "ac:", session["ac"], flush=True)
    stopped = False
    for round_index in range(args.rounds):
        if stopped:
            break
        order = configs[round_index % len(configs):] + configs[:round_index % len(configs)]
        for config in order:
            if args.only and config["name"] not in args.only:
                continue
            if on_ac() != session["ac"]:
                # AC and battery rounds are not comparable; stop instead of mixing them.
                session["stopped"] = f"power source changed before round {round_index} {config['name']}"
                print(session["stopped"], flush=True)
                stopped = True
                break
            waited, load = 0, cpu_load()
            while load > args.max_load and waited < args.max_wait:
                print(f"  load {load:.0f}% > {args.max_load}%: waiting", flush=True)
                time.sleep(10)
                waited += 10
                load = cpu_load()
            summary = out / f"{config['name']}-r{round_index}.json"
            command = [config.get("python", sys.executable), str(ROOT / "tools" / "profile_frame.py"), "cpu",
                       *config["args"], "--frames", str(args.frames), "--warmup", str(args.warmup),
                       "--summary-json", str(summary), "--log-level", "error"]
            env = dict(os.environ, **config.get("env", {}))
            started = time.time()
            completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=args.timeout)
            record = {"name": config["name"], "round": round_index, "load_before": load, "waited_s": waited,
                      "seconds": time.time() - started, "exit": completed.returncode,
                      "ended": time.strftime("%H:%M:%S"), "ac_after": on_ac(),
                      "summary": json.loads(summary.read_text()) if summary.exists() else None}
            if completed.returncode or record["summary"] is None:
                record["stderr"] = completed.stderr[-2000:]
            with open(log, "a") as stream:
                stream.write(json.dumps(record) + "\n")
            fi = (record["summary"] or {}).get("phases", {}).get("frame_info", {}).get("p50")
            print(f"round {round_index} {config['name']:28s} exit={completed.returncode} load={load:4.0f}% "
                  f"frame_info_p50={fi if fi is None else round(fi, 3)} ({record['seconds']:.0f} s)", flush=True)
    session["end"] = time.strftime("%Y-%m-%d %H:%M:%S")
    session["power_end"] = power_state()
    (out / "session.json").write_text(json.dumps(session, indent=1))
    print(table(out))


def table(out, drop=()):
    out = Path(out)
    runs = [json.loads(line) for line in (out / "runs.jsonl").read_text().splitlines() if line.strip()]
    session = json.loads((out / "session.json").read_text())
    # A run that ended on another power source than the session start is not comparable.
    runs = [r for r in runs if f"{r['name']}:{r['round']}" not in drop
            and r.get("ac_after", session.get("ac")) == session.get("ac")]
    names = [c["name"] for c in session["configs"]]
    power = {True: "AC", False: "battery", None: "unknown"}[session.get("ac")] if "ac" in session else str(session.get("power"))
    lines = [f"Session {session.get('start')} to {session.get('end', '?')}; power {power}; "
             + (f"dropped runs: {', '.join(drop)}; " if drop else "")
             + (f"{session['stopped']}; " if session.get("stopped") else "") + 
             f"{session['rounds']} rounds x {session['frames']} frames after {session['warmup']} warmup.",
             "Cells: median of per-round values [min-max of per-round values], ms. '-' = not measured.", ""]
    header = "| config | " + " | ".join(label for _, _, label in METRICS) + " | runs | late |"
    lines += [header, "|" + " --- |" * (len(METRICS) + 3)]
    for name in names:
        ok = [r for r in runs if r["name"] == name and r.get("summary")]
        cells = []
        for key, stat, _ in METRICS:
            values = [r["summary"]["phases"][key][stat] for r in ok if key in r["summary"]["phases"]]
            if values:
                cells.append(f"{statistics.median(values):.3f} [{min(values):.3f}-{max(values):.3f}]")
            else:
                cells.append("-")
        late = sum(r["summary"].get("late_frames", 0) for r in ok)
        failed = sum(1 for r in runs if r["name"] == name and not r.get("summary"))
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {len(ok)}{f' ({failed} failed)' if failed else ''} | {late} |")
    waits = [r for r in runs if r.get("waited_s")]
    loads = [r["load_before"] for r in runs]
    if loads:
        lines.append(f"\nCPU load before runs: median {statistics.median(loads):.0f}%, max {max(loads):.0f}%; "
                     f"{len(waits)} runs waited for load to drop.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("matrix")
    run.add_argument("--out", required=True)
    run.add_argument("--rounds", type=int, default=5)
    run.add_argument("--frames", type=int, default=600)
    run.add_argument("--warmup", type=int, default=120)
    run.add_argument("--max-load", type=float, default=35, help="CPU load percent that pauses the run")
    run.add_argument("--max-wait", type=float, default=120, help="seconds to wait for load to drop")
    run.add_argument("--timeout", type=float, default=300)
    run.add_argument("--only", nargs="*", help="run only these configuration names")
    tab = sub.add_parser("table")
    tab.add_argument("out")
    tab.add_argument("--drop", nargs="*", default=[], help="NAME:ROUND runs to leave out, for example "
                     "runs taken after a power-source change in a session without per-run records")
    args = parser.parse_args()
    if args.command == "run":
        cmd_run(args)
    else:
        print(table(args.out, args.drop))


if __name__ == "__main__":
    main()
