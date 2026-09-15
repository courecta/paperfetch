from __future__ import annotations

import json
from pathlib import Path

from paperfetch_app.mcp_stdio import _handle_tool
from paperfetch_app.opencode import install_mcp


def test_install_mcp_project_scope(tmp_path: Path):
    summary = install_mcp(project_dir=tmp_path, library_dir=tmp_path / "lib", marker_venv=tmp_path / "venv")
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
    install_mcp(project_dir=tmp_path, library_dir=tmp_path / "lib", install_skill=False)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["model"] == "provider/model"
    assert "other" in config["mcp"]
    assert "paperfetch" in config["mcp"]


def test_install_mcp_rejects_invalid_json(tmp_path: Path):
    (tmp_path / "opencode.json").write_text("{not json")
    try:
        install_mcp(project_dir=tmp_path, library_dir=tmp_path / "lib")
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
