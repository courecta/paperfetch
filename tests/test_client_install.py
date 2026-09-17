from __future__ import annotations

import json
from pathlib import Path

import pytest

from paperfetch_app.clients import install_mcp
from paperfetch_app.mcp_stdio import _handle_tool


def test_install_mcp_project_scope(tmp_path: Path):
    summary = install_mcp(
        client="opencode", project_dir=tmp_path, library_dir=tmp_path / "lib", marker_venv=tmp_path / "venv"
    )
    config_path = Path(summary["config_path"])
    assert config_path.is_file()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["$schema"] == "https://opencode.ai/config.json"
    entry = config["mcp"]["paperfetch"]
    assert entry["type"] == "local"
    assert entry["command"][0]
    assert "mcp" in entry["command"]
    skill = Path(summary["skill_path"])
    assert skill.is_file()
    assert "paperfetch" in skill.read_text(encoding="utf-8")


def test_install_mcp_merges_existing_config(tmp_path: Path):
    config_path = tmp_path / "opencode.json"
    config_path.write_text(json.dumps({"model": "provider/model", "mcp": {"other": {"type": "remote", "url": "https://x"}}}))
    install_mcp(client="opencode", project_dir=tmp_path, library_dir=tmp_path / "lib", install_skill=False)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["model"] == "provider/model"
    assert "other" in config["mcp"]
    assert "paperfetch" in config["mcp"]


def test_install_mcp_rejects_invalid_json(tmp_path: Path):
    (tmp_path / "opencode.json").write_text("{not json")
    try:
        install_mcp(client="opencode", project_dir=tmp_path, library_dir=tmp_path / "lib")
    except ValueError as exc:
        assert "not valid JSON" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_mcp_handle_tool_reads_bundle(tmp_path: Path):
    from tests.test_service_db import _make_bundle

    _make_bundle(tmp_path)
    content = _handle_tool(tmp_path, tmp_path / "venv", "paperfetch_grep", {"query": "anomaly", "limit": 5})
    assert "anomaly" in content[0]["text"].lower()

    content = _handle_tool(tmp_path, tmp_path / "venv", "paperfetch_outline", {"key": "abc123"})
    assert "Introduction" in content[0]["text"]

    content = _handle_tool(tmp_path, tmp_path / "venv", "paperfetch_figure", {"key": "abc123", "figure": "Figure 1"})
    assert any(item["type"] == "image" for item in content)

    content = _handle_tool(tmp_path, tmp_path / "venv", "paperfetch_table", {"key": "abc123", "table": "Table 1"})
    assert "Metric" in content[0]["text"]


def test_mcp_handle_tool_error_is_typed(tmp_path: Path):
    from paperfetch_app.errors import PaperfetchError

    try:
        _handle_tool(tmp_path, tmp_path, "paperfetch_read", {"key": "missing"})
    except PaperfetchError as exc:
        assert "missing" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected PaperfetchError")


def test_claude_code_project_scope_writes_mcp_json(tmp_path: Path):
    summary = install_mcp(
        client="claude-code", project_dir=tmp_path, library_dir=tmp_path / "lib", marker_venv=tmp_path / "venv"
    )
    config_path = Path(summary["config_path"])
    assert config_path.name == ".mcp.json"

    entry = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["paperfetch"]
    # Claude clients split the executable from its arguments, unlike opencode.
    assert isinstance(entry["command"], str)
    assert entry["args"][0] != entry["command"]
    assert "mcp" in entry["args"]
    assert str(tmp_path / "lib") in entry["args"]

    skill = Path(summary["skill_path"])
    assert skill.parent.parent.parent.name == ".claude"
    assert "paperfetch" in skill.read_text(encoding="utf-8")


def test_claude_code_preserves_other_servers(tmp_path: Path):
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}))
    install_mcp(client="claude-code", project_dir=tmp_path, library_dir=tmp_path / "lib", install_skill=False)
    servers = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"other", "paperfetch"}


def test_claude_desktop_is_always_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr("sys.platform", "linux")
    summary = install_mcp(
        client="claude-desktop", scope="project", project_dir=tmp_path, library_dir=tmp_path / "lib"
    )
    # Desktop has one config for the whole app, so a project scope is meaningless.
    assert summary["scope"] == "global"
    config_path = Path(summary["config_path"])
    assert config_path.name == "claude_desktop_config.json"
    assert "paperfetch" in json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]
    # It loads skills through its own UI, so none is written to disk.
    assert summary["skill_path"] is None


def test_unknown_client_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="Unknown client"):
        install_mcp(client="emacs", project_dir=tmp_path)


class TestServerInstructions:
    """MCP's initialize response carries orientation for the model."""

    def test_shared_library_warning_appears_when_a_project_dir_is_configured(self, tmp_path: Path):
        from paperfetch_app.mcp_stdio import server_instructions

        text = server_instructions(tmp_path / "lib", tmp_path / "papers", "both")
        assert "hardlinks into the shared library" in text
        assert "Deleting a link is safe" in text
        assert str(tmp_path / "lib") in text

    def test_no_link_warning_without_project_files(self, tmp_path: Path):
        from paperfetch_app.mcp_stdio import server_instructions

        # Nothing is materialized, so there are no links to warn about.
        text = server_instructions(tmp_path / "lib", None, "none")
        assert "hardlinks into the shared library" not in text
        assert "Project copies:" not in text
        assert str(tmp_path / "lib") in text

    def test_reading_protocol_is_stated(self, tmp_path: Path):
        from paperfetch_app.mcp_stdio import server_instructions

        text = server_instructions(tmp_path / "lib", None, "none")
        assert "paperfetch_outline" in text and "paperfetch_read" in text
        assert "background" in text

    def test_skill_carries_the_same_warning(self, tmp_path: Path):
        summary = install_mcp(
            client="claude-code", project_dir=tmp_path, library_dir=tmp_path / "lib", out_dir=tmp_path / "papers"
        )
        skill = Path(summary["skill_path"]).read_text(encoding="utf-8")
        # Clients that ignore `instructions` still get it via the skill.
        assert "hardlinks into that library" in skill
        assert "background" in skill
