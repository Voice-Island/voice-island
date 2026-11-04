import json

import settings


def test_default_llm_is_apache_licensed_qwen3():
    assert settings.DEFAULTS["llm"]["model"] == "mlx-community/Qwen3-4B-Instruct-2507-4bit"


def test_defaults_contain_no_personal_data():
    text = json.dumps(settings.DEFAULTS).lower()
    for personal in ("/users/", "@gmail", "@outlook", "@icloud"):
        assert personal not in text


def test_saved_settings_are_merged_with_defaults(isolated_settings):
    isolated_settings.write_text(json.dumps({"llm": {"temperature": 0.9}}))
    loaded = settings.load_settings()
    assert loaded["llm"]["temperature"] == 0.9
    assert loaded["llm"]["model"] == settings.DEFAULTS["llm"]["model"]  # missing setting: default value
    assert loaded["tools"]["enabled"] == settings.DEFAULTS["tools"]["enabled"]


def test_broken_settings_file_falls_back_to_defaults(isolated_settings):
    isolated_settings.write_text("{not json")
    assert settings.load_settings() == settings.DEFAULTS


def test_resolve_model():
    assert settings.resolve_model("local:Qwen2.5-1.5B-Instruct-4bit-tools") == str(
        settings.LOCAL_MODELS / "Qwen2.5-1.5B-Instruct-4bit-tools")
    assert settings.resolve_model("mlx-community/Qwen3-4B-Instruct-2507-4bit") == \
        "mlx-community/Qwen3-4B-Instruct-2507-4bit"
