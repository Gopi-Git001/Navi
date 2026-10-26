"""agent/config.py: defaults, missing key, key never leaks."""
import logging
from pathlib import Path

import pytest

from agent.config import Config, ConfigError

KEY = "sk-test-SECRET-1234567890"


def load(env, dotenv_path=None):
    return Config.load(environ=env, dotenv_path=dotenv_path)


def test_defaults():
    cfg = load({"OPENAI_API_KEY": KEY})
    assert cfg.openai_api_key == KEY
    assert cfg.ptt_key == "ctrl_r"
    assert cfg.browser_channel == "chrome"
    assert cfg.realtime_model
    assert cfg.text_model
    assert cfg.voice
    assert cfg.start_url.startswith("https://")
    assert isinstance(cfg.profile_dir, Path)
    assert cfg.profile_dir.name == ".browser_profile"


def test_overrides_from_environment():
    cfg = load({
        "OPENAI_API_KEY": KEY,
        "REALTIME_MODEL": "rt-x",
        "TEXT_MODEL": "tx-y",
        "VOICE": "alloy",
        "PTT_KEY": "f9",
        "BROWSER_CHANNEL": "msedge",
        "PROFILE_DIR": "/tmp/navi-profile",
        "START_URL": "https://example.org",
    })
    assert (cfg.realtime_model, cfg.text_model, cfg.voice) == ("rt-x", "tx-y", "alloy")
    assert cfg.ptt_key == "f9"
    assert cfg.browser_channel == "msedge"
    assert cfg.profile_dir == Path("/tmp/navi-profile")
    assert cfg.start_url == "https://example.org"


def test_blank_optional_values_use_defaults():
    cfg = load({"OPENAI_API_KEY": KEY, "PTT_KEY": " ", "BROWSER_CHANNEL": ""})
    assert cfg.ptt_key == "ctrl_r"
    assert cfg.browser_channel == "chrome"


@pytest.mark.parametrize("env", [{}, {"OPENAI_API_KEY": ""}, {"OPENAI_API_KEY": "   "}])
def test_refuses_to_start_without_key(env):
    with pytest.raises(ConfigError) as exc:
        load(env)
    assert "OPENAI_API_KEY" in str(exc.value)
    assert ".env.example" in str(exc.value)


def test_reads_dotenv_file_and_environment_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"OPENAI_API_KEY={KEY}\nVOICE=from_file\nTEXT_MODEL=file_model\n")
    cfg = load({"VOICE": "from_env"}, dotenv_path=f)
    assert cfg.openai_api_key == KEY
    assert cfg.voice == "from_env"
    assert cfg.text_model == "file_model"


def test_load_does_not_touch_process_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text(f"OPENAI_API_KEY={KEY}\n")
    load({}, dotenv_path=f)
    import os
    assert "OPENAI_API_KEY" not in os.environ


def test_key_not_in_repr_or_str():
    cfg = load({"OPENAI_API_KEY": KEY})
    assert KEY not in repr(cfg)
    assert KEY not in str(cfg)
    assert "sk-test" not in repr(cfg)


def test_key_not_in_logs_or_output(caplog, capsys, tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"OPENAI_API_KEY={KEY}\n")
    with caplog.at_level(logging.DEBUG):
        cfg = load({}, dotenv_path=f)
        logging.getLogger("navi").info("config loaded: %r", cfg)
    out = capsys.readouterr()
    assert KEY not in caplog.text
    assert KEY not in out.out + out.err


def test_missing_key_error_does_not_leak_other_secrets():
    with pytest.raises(ConfigError) as exc:
        load({"OPENAI_API_KEY": "", "SOME_OTHER": KEY})
    assert KEY not in str(exc.value)


def test_config_is_frozen():
    cfg = load({"OPENAI_API_KEY": KEY})
    with pytest.raises(Exception):
        cfg.voice = "x"  # type: ignore[misc]
