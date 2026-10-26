"""agent/session_log.py (review round 3, PR #17): status, risk and tool accept ENUMERATED values only.

Like source and reason: any other value is written as "redacted" and the field name is listed in
dropped_fields. The sets are hardcoded in the module; the contract-sync tests below fail loudly
when docs/TOOL_CONTRACT.json changes.
"""
import json
import re
from pathlib import Path

import pytest

from agent.session_log import PLACEHOLDER, RISKS, STATUSES, TOOLS, SessionLog
from common import types as common_types

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs" / "TOOL_CONTRACT.json"
CHANGED = "docs/TOOL_CONTRACT.json changed: update agent/session_log.py closed sets"
CLOSED = {"status": STATUSES, "risk": RISKS, "tool": TOOLS}


def read_lines(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


def contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


# 1. every valid value is written unchanged
@pytest.mark.parametrize("field", ["status", "risk", "tool"])
def test_every_valid_value_is_written_unchanged(tmp_path, field):
    values = CLOSED[field]
    assert isinstance(values, frozenset) and values
    p = tmp_path / "s.jsonl"
    log = SessionLog(p)
    for v in sorted(values):
        log.mark("tool_end", **{field: v})
    rows = read_lines(p)
    assert [r[field] for r in rows] == sorted(values)
    assert all("dropped_fields" not in r for r in rows)


# 2. secret-like and unknown identifier-shaped values are redacted
@pytest.mark.parametrize("field", ["status", "risk", "tool"])
@pytest.mark.parametrize("value", ["hunter2", "frobnicate", "tool.end", "x:y", "a1", "a" * 32, "Navigate", "OK", "ok "])
def test_unknown_values_are_redacted_and_never_in_the_file(tmp_path, field, value):
    p = tmp_path / "s.jsonl"
    SessionLog(p).mark("tool_end", **{field: value})
    raw = p.read_text(encoding="utf-8")
    assert value not in raw
    row = read_lines(p)[0]
    assert row[field] == PLACEHOLDER == "redacted"
    assert row["dropped_fields"] == [field]


@pytest.mark.parametrize("field", ["status", "risk", "tool"])
def test_value_valid_for_another_closed_field_is_redacted(tmp_path, field):
    # exact per-field match: "navigate" is not a status, "post" is not a tool, "ok" is not a risk
    foreign = {"status": "navigate", "risk": "ok", "tool": "post"}[field]
    p = tmp_path / "s.jsonl"
    SessionLog(p).mark("tool_end", **{field: foreign})
    row = read_lines(p)[0]
    assert row[field] == PLACEHOLDER and row["dropped_fields"] == [field]


# 3. non-strings are redacted (None and bool pass the generic rule, but not closed sets)
@pytest.mark.parametrize("field", ["status", "risk", "tool"])
@pytest.mark.parametrize("value", [1, 0, 1.5, None, True, False, ["ok"], {"a": 1}, b"ok"])
def test_non_string_values_are_redacted(tmp_path, field, value):
    p = tmp_path / "s.jsonl"
    SessionLog(p).mark("tool_end", **{field: value})
    row = read_lines(p)[0]
    assert row[field] == PLACEHOLDER
    assert row["dropped_fields"] == [field]


# 4. contract sync: a contract change must fail loudly
def test_tools_match_contract():
    assert set(TOOLS) == {t["name"] for t in contract()["tools"]}, CHANGED
    assert len(TOOLS) == 21, CHANGED


def test_risks_match_contract_and_common_types():
    expected = {"none"} | set(contract()["risk_classes_requiring_confirm"])
    assert set(RISKS) == expected, CHANGED
    assert set(RISKS) == set(common_types.RISKS), CHANGED


def test_statuses_match_contract_and_common_types():
    words = {w.strip() for w in contract()["tool_result"]["fields"]["status"].split("|")}
    assert set(STATUSES) == words, CHANGED
    assert set(STATUSES) == set(common_types.STATUSES), CHANGED


def test_closed_sets_are_identifiers():
    for name, values in (("STATUSES", STATUSES), ("RISKS", RISKS), ("TOOLS", TOOLS)):
        assert all(re.fullmatch(r"[a-z0-9_]{1,32}", v) for v in values), name
        assert PLACEHOLDER not in values, name


# 5. regression: all five closed-set fields valid still gives one whole JSON line
def test_all_closed_set_fields_valid_one_whole_line(tmp_path):
    p = tmp_path / "s.jsonl"
    SessionLog(p).mark("tool_end", source="model", reason="blocked", status="blocked",
                       risk="comment", tool="click", call_id="c1", verb="reply")
    raw = p.read_text(encoding="utf-8")
    assert raw.endswith("\n") and raw.count("\n") == 1
    row = json.loads(raw)
    assert (row["source"], row["reason"], row["status"], row["risk"], row["tool"]) == (
        "model", "blocked", "blocked", "comment", "click")
    assert "dropped_fields" not in row
