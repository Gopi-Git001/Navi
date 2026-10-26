"""One command to run every benchmark row and print a report.

    python -m benchmarks.run --fast

A row is run by an optional module ``benchmarks.measure.<id lowercase>`` that
exposes ``run(mode) -> report.Measurement``. A row with no such module is shown
as NOT IMPLEMENTED, and any such row, miss or error makes the exit code non-zero.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import report, rows

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results"
REPORTS = ROOT / "benchmarks" / "reports"
WINDOWS = ROOT / "benchmarks" / "windows_runs.json"


def load_last_run(results_dir: Path):
    files = sorted(Path(results_dir).glob("*.json"))
    if not files:
        return {}
    return json.loads(files[-1].read_text()).get("rows", {})


def _git_commit():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _chrome_version():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        exe = shutil.which(name)
        if exe:
            try:
                return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
            except Exception:
                return "unknown"
    return "not installed"


def environment(mode: str):
    return {
        "commit": _git_commit(),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "chrome_version": _chrome_version(),
        "mode": mode,
    }


def discover(mode: str, parsed):
    """Run each row's measure module. Missing module -> NOT IMPLEMENTED (key absent)."""
    results, errors = {}, {}
    for r in parsed:
        name = f"benchmarks.measure.{r.id.lower()}"
        try:
            mod = importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name in (name, "benchmarks.measure"):
                continue
            errors[r.id] = f"import failed: {e}"
            continue
        try:
            results[r.id] = mod.run(mode)
        except Exception as e:  # a crashing benchmark is a failure, never a silent skip
            errors[r.id] = f"{type(e).__name__}: {e}"
    return results, errors


def _windows_runs(path=WINDOWS):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


def run_rows(results, results_dir=RESULTS, mode="fast", save=True, stamp=None,
             errors=None, doc=rows.DEFAULT_DOC, windows_path=WINDOWS, out=print):
    errors = errors or {}
    parsed = rows.load_rows(doc)
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    prev = load_last_run(results_dir)
    windows = _windows_runs(windows_path)
    lines, saved_rows, failed = [], {}, False
    for r in parsed:
        checks = rows.CHECKS.get(r.id, [])
        last = prev.get(r.id, {}).get("text", "-") or "-"
        if r.id in errors:
            status, this = "ERROR", errors[r.id]
        elif r.id not in results:
            status, this = "NOT IMPLEMENTED", "-"
        else:
            m = results[r.id]
            status = report.status_with_history(r.id, m, checks, prev)
            this = m.text
            saved_rows[r.id] = m.to_dict()
        saved_rows.setdefault(r.id, {"stats": {}, "text": ""})["status"] = status
        if status != "MEETS" and status != "REPORTED":
            failed = True
        lines.append(report.Line(r.id, r.measure[:60], r.owner, r.target[:60], last, this, status, windows.get(r.id)))
    out(report.render_table(lines))
    counts = {}
    for l in lines:
        counts[l.status] = counts.get(l.status, 0) + 1
    out("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    if save:
        stamp = stamp or dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        payload = {"environment": environment(mode), "rows": saved_rows}
        (results_dir / f"{stamp}.json").write_text(json.dumps(payload, indent=2))
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run every Navi benchmark row and print a report.")
    ap.add_argument("--fast", action="store_true", help="fast mode: the rows that run on every merge")
    ap.add_argument("--full", action="store_true", help="full mode: all rows, long soak included")
    ap.add_argument("--results-dir", default=str(RESULTS))
    ap.add_argument("--doc", default=str(rows.DEFAULT_DOC))
    ap.add_argument("--no-save-report", action="store_true", help="do not write benchmarks/reports/<date>.md")
    args = ap.parse_args(argv)
    mode = "full" if args.full else "fast"
    parsed = rows.load_rows(args.doc)
    results, errors = discover(mode, parsed)
    lines_out = []
    code = run_rows(results, results_dir=args.results_dir, mode=mode, errors=errors,
                    doc=args.doc, out=lambda s: (lines_out.append(s), print(s)))
    if not args.no_save_report:
        REPORTS.mkdir(parents=True, exist_ok=True)
        (REPORTS / f"{dt.date.today().isoformat()}.md").write_text(
            f"# Benchmark report {dt.date.today().isoformat()} ({mode})\n\n" + "\n".join(lines_out) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
