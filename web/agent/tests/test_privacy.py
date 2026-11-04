"""P0-4 (per-installation keys) and P0-7 (no transcripts in the logs by default)."""
import logging
import os
import stat
from datetime import datetime

import pytest

import history
import lk_keys
import privacy


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setenv("VOICE_LOG_TRANSCRIPTS", "0")


@pytest.fixture
def verbose(monkeypatch):
    monkeypatch.setenv("VOICE_LOG_TRANSCRIPTS", "1")


def test_default_settings_keep_transcripts_out_of_logs(monkeypatch):
    monkeypatch.delenv("VOICE_LOG_TRANSCRIPTS", raising=False)
    from settings import DEFAULTS

    assert DEFAULTS["privacy"]["log_transcripts"] is False


def test_r_masks_text_by_default(quiet):
    assert privacy.r("Send the deck to Lea") == "‹20 car.›"
    assert privacy.r("") == ""
    assert privacy.r(None) == ""


def test_r_keeps_text_when_allowed(verbose):
    assert privacy.r("Open Notes.") == "Open Notes."


def test_livekit_pii_fields_are_masked(quiet, caplog):
    privacy.install()
    log = logging.getLogger("livekit.agents.test")
    with caplog.at_level(logging.DEBUG, logger="livekit.agents.test"):
        log.debug("item added", extra={"role": "user", "lk.pii.text": "my bank password is 1234"})
    record = caplog.records[-1]
    assert getattr(record, "lk.pii.text") == privacy.MASK
    assert record.role == "user"   # the rest of the record is untouched


def test_livekit_pii_fields_kept_when_allowed(verbose, caplog):
    privacy.install()
    log = logging.getLogger("livekit.agents.test")
    with caplog.at_level(logging.DEBUG, logger="livekit.agents.test"):
        log.debug("item added", extra={"lk.pii.text": "Open Notes."})
    assert getattr(caplog.records[-1], "lk.pii.text") == "Open Notes."


def test_history_prunes_days_older_than_the_limit(tmp_path):
    for day in ("2026-09-01", "2026-10-20", "2026-11-02"):
        (tmp_path / f"{day}.jsonl").write_text("{}\n")
    (tmp_path / "notes.jsonl").write_text("{}\n")   # not a day file: left alone
    removed = history.prune(tmp_path, days=30, today=datetime(2026, 11, 2))
    assert sorted(f.stem for f in removed) == ["2026-09-01"]
    assert sorted(f.stem for f in tmp_path.glob("*.jsonl")) == ["2026-10-20", "2026-11-02", "notes"]


def test_history_days_zero_keeps_everything(tmp_path):
    (tmp_path / "2020-01-01.jsonl").write_text("{}\n")
    assert history.prune(tmp_path, days=0, today=datetime(2026, 11, 2)) == []


def test_keys_are_random_private_and_stable(tmp_path):
    path = tmp_path / "livekit.keys"
    first = lk_keys.ensure(path)
    assert first[0] != "devkey" and first[1] != "secret"
    assert len(first[1]) >= 32
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert lk_keys.ensure(path) == first            # same keys on the next launch
    assert lk_keys.ensure(tmp_path / "other") != first  # another installation gets its own


def test_keys_file_format_is_livekit_key_file(tmp_path):
    path = tmp_path / "livekit.keys"
    key, secret = lk_keys.ensure(path)
    assert path.read_text() == f"{key}: {secret}\n"
    assert lk_keys.load(path) == (key, secret)


def test_apply_env_overrides_an_old_devkey(tmp_path, monkeypatch):
    path = tmp_path / "livekit.keys"
    key, secret = lk_keys.ensure(path)
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    assert lk_keys.apply_env(path)
    assert (os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]) == (key, secret)


def test_missing_keys_file_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "x")
    assert not lk_keys.apply_env(tmp_path / "absent")
    assert os.environ["LIVEKIT_API_KEY"] == "x"
