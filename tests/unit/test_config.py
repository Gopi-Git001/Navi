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


# ---- review round 1 (PR #17): profile dir resolution and everyday-Chrome refusal ----
from agent.config import ROOT


def test_relative_profile_dir_resolves_against_repo_root_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load({"OPENAI_API_KEY": KEY, "PROFILE_DIR": "my_profile"})
    assert cfg.profile_dir == ROOT / "my_profile"
    assert cfg.profile_dir.is_absolute()


def test_default_profile_dir_is_dedicated_one_under_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load({"OPENAI_API_KEY": KEY})
    assert cfg.profile_dir == ROOT / ".browser_profile"


def test_dotdot_in_relative_profile_dir_is_normalised():
    cfg = load({"OPENAI_API_KEY": KEY, "PROFILE_DIR": "a/../.browser_profile"})
    assert cfg.profile_dir == ROOT / ".browser_profile"


CHROME_PATHS = [
    r"C:\Users\Gopi\AppData\Local\Google\Chrome\User Data",
    r"C:\Users\Gopi\AppData\Local\Google\Chrome\User Data\Default",
    r"c:\users\gopi\appdata\local\google\chrome\user data\Profile 1",
    r"C:\USERS\GOPI\APPDATA\LOCAL\GOOGLE\CHROME\USER DATA",
    "C:/Users/Gopi/AppData/Local/Google/Chrome/User Data/Default",
    "C:\\Users\\Gopi\\AppData\\Local\\Google\\Chrome\\User Data\\\\",
    r"%LOCALAPPDATA%\Google\Chrome\User Data",
    r"C:\Users\Gopi\AppData\Local\Google\Chrome\..\Chrome\User Data",
    "/home/gopi/.config/google-chrome",
    "/home/gopi/.config/google-chrome/Default",
    "/home/gopi/.config/Google-Chrome/Default",
    "/home/gopi/.config/chromium",
    "/home/gopi/.config/chromium/Profile 2",
    "/Users/gopi/Library/Application Support/Google/Chrome",
    "/Users/gopi/Library/Application Support/Google/Chrome/Default",
    "/users/gopi/library/application support/google/chrome/default",
    "~/.config/google-chrome",
    "~/.config/chromium/Default",
    "~/Library/Application Support/Google/Chrome",
    "/home/gopi/.config/google-chrome/../google-chrome/Default",
]


@pytest.mark.parametrize("path", CHROME_PATHS)
def test_refuses_everyday_chrome_profile(path):
    with pytest.raises(ConfigError) as exc:
        load({"OPENAI_API_KEY": KEY, "PROFILE_DIR": path})
    assert ".browser_profile" in str(exc.value)
    assert KEY not in str(exc.value)


def test_refuses_everyday_chrome_profile_from_dotenv_and_real_home(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text(f"OPENAI_API_KEY={KEY}\nPROFILE_DIR=/home/gopi/.config/google-chrome\n")
    with pytest.raises(ConfigError):
        load({}, dotenv_path=f)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ConfigError):
        load({"OPENAI_API_KEY": KEY, "PROFILE_DIR": str(tmp_path / ".config" / "chromium")})


def test_refuses_when_localappdata_env_points_at_chrome(tmp_path):
    lad = tmp_path / "Local"
    target = lad / "Google" / "Chrome" / "User Data" / "Default"
    with pytest.raises(ConfigError):
        load({"OPENAI_API_KEY": KEY, "LOCALAPPDATA": str(lad), "PROFILE_DIR": str(target)})


@pytest.mark.parametrize("path", [
    "/tmp/navi-profile",
    "/home/gopi/.config/google-chrome-navi",
    "/home/gopi/.config/chromium-navi/Default",
    r"C:\Users\Gopi\AppData\Local\Google\Chrome Navi\User Data",
    r"C:\Users\Gopi\navi\.browser_profile",
    ".browser_profile",
])
def test_similar_but_different_paths_are_allowed(path):
    assert load({"OPENAI_API_KEY": KEY, "PROFILE_DIR": path}).profile_dir


def test_empty_profile_dir_falls_back_to_dedicated_default():
    assert load({"OPENAI_API_KEY": KEY, "PROFILE_DIR": ""}).profile_dir == ROOT / ".browser_profile"
