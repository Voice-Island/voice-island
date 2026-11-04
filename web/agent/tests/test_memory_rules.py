""""Remember…": remember, list, forget, and the note recalled to the LLM."""

import memory_rules


def test_remember_list_and_forget():
    assert memory_rules.handle("Remember that my work account is Acme.") == (
        "Okay, I will remember: My work account is Acme.")
    assert memory_rules.handle("Please remember I prefer Chrome on the left") == (
        "Okay, I will remember: I prefer Chrome on the left.")
    assert "Acme" in memory_rules.handle("What do you remember?")
    assert "Acme" in memory_rules.note() and "Chrome" in memory_rules.note()
    assert memory_rules.handle("Forget that my work account is Acme") == (
        "Forgot: My work account is Acme.")
    assert "Acme" not in memory_rules.note()
    assert memory_rules.handle("Forget about pizza") == "I had nothing like that in memory."


def test_ordinary_requests_are_not_memory():
    for text in ["Open Chrome.", "Do you remember the capital of France?", "Forget focus mode."]:
        assert memory_rules.handle(text) is None
