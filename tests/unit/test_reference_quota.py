"""Quota stop in reference/main.py."""
import importlib.util
import os
import pathlib
import sys

import pytest

REF = pathlib.Path(__file__).resolve().parents[2] / "reference"


def _load(name):
    sys.path.insert(0, str(REF))
    try:
        spec = importlib.util.spec_from_file_location(f"ref_{name}", REF / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(REF))


@pytest.fixture(scope="module")
def ref_main():
    return _load("main")


def test_close_reason_quota(ref_main):
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close
    assert ref_main.is_quota_error(ConnectionClosedError(Close(1008, "insufficient_quota"), None))


def test_http_429_body_quota(ref_main):
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response
    r = Response(429, "x", Headers(), b'{"error":{"code":"insufficient_quota"}}')
    assert ref_main.is_quota_error(InvalidStatus(r))


def test_chained_cause_quota(ref_main):
    e = ConnectionError("wrapped")
    e.__cause__ = RuntimeError("You exceeded your current quota")
    assert ref_main.is_quota_error(e)


@pytest.mark.parametrize("exc", [OSError("network down"), ConnectionError("realtime connection closed"), TimeoutError()])
def test_other_errors_still_reconnect(ref_main, exc):
    assert not ref_main.is_quota_error(exc)


def test_plain_close_and_500_not_quota(ref_main):
    from websockets.datastructures import Headers
    from websockets.exceptions import ConnectionClosedError, InvalidStatus
    from websockets.frames import Close
    from websockets.http11 import Response
    assert not ref_main.is_quota_error(ConnectionClosedError(Close(1006, ""), None))
    assert not ref_main.is_quota_error(InvalidStatus(Response(500, "x", Headers(), b"")))


def test_cyclic_chain_terminates(ref_main):
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__cause__, b.__cause__ = b, a
    assert not ref_main.is_quota_error(a)
