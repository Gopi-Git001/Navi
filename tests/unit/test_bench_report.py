"""Unit tests for the benchmark harness math, parsing and report (issue #5).

These tests are written first and must be able to fail: every one checks a
number or a text the harness prints.
"""
import json
import math
from pathlib import Path

import pytest

from benchmarks import report, rows, run
from benchmarks.report import Measurement

REPO = Path(__file__).resolve().parents[2]


# ---------- statistics ----------

def test_median_odd_and_even():
    assert report.median([3, 1, 2]) == 2
    assert report.median([4, 1, 3, 2]) == 2.5


def test_p95_is_nearest_rank():
    # nearest rank: ceil(0.95 * 20) = 19th value of 20
    vals = list(range(1, 21))
    assert report.p95(vals) == 19
    # a single value is its own p95
    assert report.p95([7]) == 7


def test_max_value():
    assert report.max_value([5, 9, 2]) == 9


@pytest.mark.parametrize("fn", [report.median, report.p95, report.max_value])
def test_empty_input_raises_instead_of_inventing_a_number(fn):
    with pytest.raises(ValueError):
        fn([])


def test_wilson_interval_known_values():
    lo, hi = report.wilson(0, 100)
    assert lo == 0.0
    assert hi == pytest.approx(0.0370, abs=0.001)
    lo, hi = report.wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=0.002)
    assert hi == pytest.approx(0.5962, abs=0.002)


def test_wilson_zero_denominator_is_rejected():
    with pytest.raises(ValueError):
        report.wilson(0, 0)


def test_wilson_numerator_cannot_exceed_denominator():
    with pytest.raises(ValueError):
        report.wilson(5, 4)


# ---------- text ----------

def test_rate_text_shows_numerator_over_denominator():
    text = report.rate_text(3, 120)
    assert text.startswith("3/120")
    assert "2.5%" in text
    assert "95% CI" in text


def test_rate_text_with_no_trials_says_no_data():
    assert report.rate_text(0, 0) == "no data (0/0)"


def test_zero_events_upper_bound_text():
    assert report.zero_upper_text(1.0) == "0 in 1 h, 95% upper bound 3 per hour"
    assert report.zero_upper_text(0.5) == "0 in 0.5 h, 95% upper bound 6 per hour"


def test_zero_upper_text_needs_positive_time():
    with pytest.raises(ValueError):
        report.zero_upper_text(0)


# ---------- evaluation against targets ----------

def test_times_meeting_target_pass():
    m = Measurement.from_times([300, 400, 500, 600, 650] * 4)
    checks = [("median", "<=", 700), ("p95", "<=", 1000)]
    assert report.evaluate(checks, m) == "MEETS"


def test_times_missing_p95_fail():
    m = Measurement.from_times([300] * 18 + [1200, 1300])
    checks = [("median", "<=", 700), ("p95", "<=", 1000)]
    assert report.evaluate(checks, m) == "MISSES"


def test_count_target_zero_fails_on_one_event():
    assert report.evaluate([("count", "==", 0)], Measurement.from_count(1)) == "MISSES"
    assert report.evaluate([("count", "==", 0)], Measurement.from_count(0)) == "MEETS"


def test_rate_target_uses_numerator_and_denominator():
    ok = Measurement.from_rate(97, 100)
    bad = Measurement.from_rate(94, 100)
    assert report.evaluate([("rate", ">=", 0.95)], ok) == "MEETS"
    assert report.evaluate([("rate", ">=", 0.95)], bad) == "MISSES"


def test_report_only_row_is_reported_not_judged():
    assert report.evaluate([], Measurement.from_times([100])) == "REPORTED"


def test_measurement_missing_the_stat_the_target_needs_is_a_miss():
    # a count measurement cannot satisfy a p95 target
    assert report.evaluate([("p95", "<=", 100)], Measurement.from_count(0)) == "MISSES"


# ---------- rows parsed from docs/BENCHMARKS.md ----------

def test_rows_parse_every_id_in_benchmarks_md():
    parsed = rows.load_rows(REPO / "docs" / "BENCHMARKS.md")
    ids = [r.id for r in parsed]
    for expected in ["A1", "A1b", "A4", "B1", "B2", "C4", "C5", "C13", "D6", "E4"]:
        assert expected in ids
    assert len(ids) == len(set(ids)), "row ids must be unique"


def test_every_row_names_an_owner_module():
    for r in rows.load_rows(REPO / "docs" / "BENCHMARKS.md"):
        assert r.owner in {"voice/", "browser/", "agent/", "research/"}, r.id


def test_every_row_has_a_machine_check_or_is_marked_report_only():
    for r in rows.load_rows(REPO / "docs" / "BENCHMARKS.md"):
        assert r.id in rows.CHECKS, f"{r.id} has no entry in rows.CHECKS"


def test_checks_match_the_numbers_gopi_set():
    c = rows.CHECKS
    assert ("median", "<=", 700) in c["A1"] and ("p95", "<=", 1000) in c["A1"]
    assert ("p95", "<=", 500) in c["B1"]
    assert ("max", "<=", 1000) in c["B2"]
    assert ("p95", "<=", 25000) in c["A4"]
    assert c["C4"] == [("count", "==", 0)]
    assert ("rate", "<=", 0.02) in c["D6"]


def test_unknown_table_is_an_error(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("# nothing here\n")
    with pytest.raises(ValueError):
        rows.load_rows(p)


# ---------- the run: empty system ----------

def test_empty_system_lists_every_row_as_not_implemented_and_fails(capsys, tmp_path):
    code = run.main(["--fast", "--results-dir", str(tmp_path), "--no-save-report"])
    out = capsys.readouterr().out
    assert code != 0
    for row_id in ["A1", "A1b", "B1", "C4", "D6", "E4"]:
        assert row_id in out
    assert out.count("NOT IMPLEMENTED") >= len(rows.load_rows(REPO / "docs" / "BENCHMARKS.md"))
    for owner in ["voice/", "browser/", "agent/", "research/"]:
        assert owner in out
    assert "Windows-verified" in out


def test_run_with_one_missed_target_exits_nonzero(tmp_path):
    results = {"B1": Measurement.from_times([100, 200, 900])}  # p95 900 > 500
    code = run.run_rows(results, results_dir=tmp_path, mode="fast", save=False)
    assert code != 0


def test_run_exit_code_zero_only_when_every_row_meets_or_is_reported(tmp_path):
    parsed = rows.load_rows(REPO / "docs" / "BENCHMARKS.md")
    fake = {}
    for r in parsed:
        checks = rows.CHECKS[r.id]
        fake[r.id] = _passing_measurement(checks)
    code = run.run_rows(fake, results_dir=tmp_path, mode="fast", save=False)
    assert code == 0


def _passing_measurement(checks):
    stats = {c[0] for c in checks}
    if "count" in stats:
        return Measurement.from_count(0)
    if "rate" in stats:
        ops = {c[1] for c in checks}
        return Measurement.from_rate(0, 1000) if "<=" in ops else Measurement.from_rate(1000, 1000)
    if "drift" in stats:
        return Measurement.from_drift(0.0)
    if "growth_mb" in stats:
        return Measurement.from_growth_mb(0.0)
    return Measurement.from_times([1, 2, 3])


# ---------- saved results and last-run comparison ----------

def test_results_saved_as_json_with_environment_record(tmp_path):
    results = {"B1": Measurement.from_times([100, 200, 300])}
    run.run_rows(results, results_dir=tmp_path, mode="fast", save=True)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    env = data["environment"]
    for key in ["commit", "os", "chrome_version", "mode"]:
        assert key in env
    assert env["mode"] == "fast"
    assert data["rows"]["B1"]["stats"]["p95"] == 300


def test_last_run_column_comes_from_previous_json(tmp_path):
    first = {"B1": Measurement.from_times([100, 200, 300])}
    run.run_rows(first, results_dir=tmp_path, mode="fast", save=True, stamp="20261025T100000")
    prev = run.load_last_run(tmp_path)
    assert prev["B1"]["stats"]["p95"] == 300


def test_worse_than_last_run_is_flagged_even_if_target_met(tmp_path):
    prev = {"B1": {"stats": {"p95": 100.0}}}
    now = Measurement.from_times([100, 200, 400])  # p95 400 <= 500 meets, but worse
    status = report.status_with_history("B1", now, rows.CHECKS["B1"], prev)
    assert status == "WORSE"


def test_windows_verified_column_empty_until_a_laptop_run_is_added(tmp_path):
    table = report.render_table(
        [report.Line("B1", "stop", "voice/", "p95 ≤ 500 ms", "-", "p95 300 ms", "MEETS", None)]
    )
    assert "Windows-verified" in table
    line = [l for l in table.splitlines() if l.startswith("| B1")][0]
    assert line.rstrip().endswith("— |")


def test_windows_verified_shows_pass_when_laptop_run_exists():
    table = report.render_table(
        [report.Line("B1", "stop", "voice/", "p95 ≤ 500 ms", "-", "p95 300 ms", "MEETS", "2026-10-26 pass")]
    )
    assert "2026-10-26 pass" in table
