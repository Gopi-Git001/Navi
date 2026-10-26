"""common/types.py must match docs/TOOL_CONTRACT.json (the test reads the JSON)."""
import dataclasses
import json
import re
import typing
from pathlib import Path

import pytest

from common.types import RISKS, STATUSES, ActionDescription, Gated, ToolResult

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs" / "TOOL_CONTRACT.json"


@pytest.fixture(scope="module")
def contract():
    data = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    # Fail loudly (never skip, never pass silently) if the file is still the stub.
    if not data.get("tools") or "tool_result" not in data:
        pytest.fail(
            f"{CONTRACT_PATH} is still a stub (no tools / tool_result). "
            "The final contract lives in PR #3 (branch architect/spec-and-plan)."
        )
    return data


def contract_statuses(contract):
    return [s.strip() for s in contract["tool_result"]["fields"]["status"].split("|")]


def contract_gate_keys(contract):
    m = re.search(r"\{([^}]*)\}", contract["tool_result"]["fields"]["gate"])
    assert m, "contract gate field no longer lists keys in braces"
    return [k.strip() for k in m.group(1).split(",")]


def test_status_literals_match_contract(contract):
    assert list(STATUSES) == contract_statuses(contract)
    assert set(typing.get_args(typing.get_type_hints(ToolResult)["status"])) == set(STATUSES)


def test_toolresult_fields_match_contract(contract):
    names = [f.name for f in dataclasses.fields(ToolResult)]
    assert names == list(contract["tool_result"]["fields"].keys())
    assert ToolResult.__dataclass_params__.frozen


def test_gate_and_action_description_fields_match_contract(contract):
    keys = contract_gate_keys(contract)
    assert [f.name for f in dataclasses.fields(ActionDescription)] == keys
    assert ActionDescription.__dataclass_params__.frozen
    # The four required by the issue have no defaults; readback is filled by code later.
    required = [f.name for f in dataclasses.fields(ActionDescription)
                if f.default is dataclasses.MISSING]
    assert required == ["risk", "verb", "target", "text"]


def test_risks_match_contract(contract):
    gated = set(contract["risk_classes_requiring_confirm"])
    assert set(RISKS) == gated | {"none"}
    # every tool risk label in the contract is one the code knows how to treat
    assert {t["risk"] for t in contract["tools"]} <= {"none", "may_gate"}


def desc(**kw):
    base = dict(risk="comment", verb="post", target="Reply button", text="hello")
    base.update(kw)
    return ActionDescription(**base)


def test_toolresult_basic_and_defaults():
    r = ToolResult(status="ok", text="ok: done")
    assert r.image_b64 is None and r.gate is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.status = "error"  # type: ignore[misc]


@pytest.mark.parametrize("status", STATUSES)
def test_every_contract_status_is_accepted(status):
    gate = desc() if status == "awaiting_confirmation" else None
    assert ToolResult(status=status, text=status, gate=gate).status == status


def test_unknown_status_rejected():
    with pytest.raises(ValueError):
        ToolResult(status="success", text="x")


def test_gate_only_with_awaiting_confirmation():
    with pytest.raises(ValueError):
        ToolResult(status="ok", text="ok", gate=desc())
    with pytest.raises(ValueError):
        ToolResult(status="awaiting_confirmation", text="awaiting_confirmation")
    r = ToolResult(status="awaiting_confirmation", text="awaiting_confirmation", gate=desc())
    assert r.gate.verb == "post"


def test_unknown_risk_rejected():
    with pytest.raises(ValueError):
        desc(risk="yolo")


def test_gated_carries_description_and_is_an_exception():
    d = desc(text="SECRET REPLY TEXT")
    g = Gated(d)
    assert isinstance(g, Exception)
    assert g.description is d
    with pytest.raises(Gated) as exc:
        raise g
    assert exc.value.description.target == "Reply button"
    # message must not carry typed text (it may end up in logs)
    assert "SECRET REPLY TEXT" not in str(g)
    assert "SECRET REPLY TEXT" not in repr(g)


def test_repr_hides_image_payload():
    r = ToolResult(status="ok", text="ok", image_b64="A" * 5000)
    assert "AAAA" not in repr(r)


# ---- review round 1 (PR #17): first line of text starts with the status word ----
def _gate_for(status):
    return desc() if status == "awaiting_confirmation" else None


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("text", [
    "{s}", "{s}: done", "{s}\nsecond line", "{s} - details", "{s}:\n\nmore",
])
def test_text_first_line_starting_with_status_is_accepted(status, text):
    t = text.format(s=status)
    assert ToolResult(status=status, text=t, gate=_gate_for(status)).text == t


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("text", [
    "", "   ", "done", "Something went wrong", "\n{s}: leading newline",
    " {s}: leading space", "{S}: wrong case", "{s}x: no word boundary",
    "{s}_x", "line one\n{s}: status only on the second line",
])
def test_text_not_starting_with_status_is_rejected(status, text):
    bad = text.format(s=status, S=status.upper())
    with pytest.raises(ValueError) as exc:
        ToolResult(status=status, text=bad, gate=_gate_for(status))
    assert status in str(exc.value)


def test_another_status_word_is_not_accepted():
    with pytest.raises(ValueError):
        ToolResult(status="ok", text="error: it failed")
    with pytest.raises(ValueError):
        ToolResult(status="blocked", text="cancelled: stopped")


def test_error_message_does_not_echo_text():
    with pytest.raises(ValueError) as exc:
        ToolResult(status="ok", text="SECRET PAGE TEXT")
    assert "SECRET" not in str(exc.value)


def test_readback_is_kept_on_action_description():
    d = desc(readback="Post this comment: hello")
    assert d.readback == "Post this comment: hello"
    assert [f.name for f in dataclasses.fields(ActionDescription)][-1] == "readback"
