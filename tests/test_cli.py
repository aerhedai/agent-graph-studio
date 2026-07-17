from __future__ import annotations

import json

import backend.llm.anthropic_client as anthropic_client_module
from backend.cli.main import main
from backend.llm.client import LLMResponse
from conftest import FIXTURES_DIR


class _FakeAnthropicLLMClient:
    def __init__(self, api_key: str | None = None) -> None:
        pass

    def complete(self, *, model, system_prompt, prompt, max_tokens):
        return LLMResponse(text="cli mocked reply", input_tokens=3, output_tokens=4)


def test_cli_runs_valid_graph_and_prints_trace(capsys, monkeypatch):
    # The anthropic connection type's build_client() does a fresh
    # `backend.llm.anthropic_client.AnthropicLLMClient` lookup at call time --
    # patch the module attribute so the fake is picked up, keeping this test
    # offline. valid_linear.json's llm_call node references "test-connection",
    # so the store needs a matching (anthropic-typed) entry.
    from backend.connections.store import add_connection

    monkeypatch.setattr(anthropic_client_module, "AnthropicLLMClient", _FakeAnthropicLLMClient)
    add_connection("test-connection", "anthropic", {"api_key": "unused-in-tests"})

    exit_code = main([str(FIXTURES_DIR / "valid_linear.json")])

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert "trace" in payload
    assert "result" in payload
    assert payload["result"]["n3"] == "cli mocked reply"


def test_cli_exits_nonzero_on_missing_input(capsys):
    exit_code = main([str(FIXTURES_DIR / "missing_input.json")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "missing_required_input" in captured.err


def test_cli_exits_nonzero_on_malformed_json(tmp_path, capsys):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json")

    exit_code = main([str(bad_file)])

    captured = capsys.readouterr()
    assert exit_code == 2


def test_cli_exits_nonzero_on_wrong_arg_count(capsys):
    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Usage" in captured.err
