"""load_dotenv override in reference/config.py."""
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


def test_env_file_overrides_shell(tmp_path, monkeypatch):
    cfg = _load("config")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=from-file\nOUTPUT_DIR=%s\nPROFILE_DIR=%s\n" % (tmp_path / "o", tmp_path / "p"))
    monkeypatch.setattr(cfg, "ROOT", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "stale-from-shell")
    assert cfg.Config.load().openai_api_key == "from-file"


def test_missing_key_still_exits(tmp_path, monkeypatch):
    cfg = _load("config")
    monkeypatch.setattr(cfg, "ROOT", tmp_path)  # no .env here
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        cfg.Config.load()
