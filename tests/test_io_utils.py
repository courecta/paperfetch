from __future__ import annotations

from pathlib import Path

import pytest

from paperfetch_app.io_utils import (
    check_markdown_quality,
    ensure_dir,
    now_utc_iso,
    retry_call,
    safe_slug,
    sha256_file,
    sha256_text,
    write_json_atomic,
)


class TestSafeSlug:
    def test_basic(self):
        assert safe_slug("Hello World") == "hello-world"

    def test_multiple_spaces(self):
        assert safe_slug("Hello   World") == "hello-world"

    def test_special_chars(self):
        assert safe_slug("Hello@World#2024!") == "hello-world-2024"

    def test_empty(self):
        assert safe_slug("") == "paper"

    def test_only_special(self):
        assert safe_slug("!!!") == "paper"

    def test_leading_trailing_dashes(self):
        assert safe_slug("---hello---") == "hello"


class TestSha256:
    def test_text(self):
        result = sha256_text("hello")
        assert len(result) == 64
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_file(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = sha256_file(f)
        assert len(result) == 64
        assert result == sha256_text("hello")

    def test_file_binary(self, tmp_path: Path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        result = sha256_file(f)
        assert len(result) == 64


class TestNowUtcIso:
    def test_format(self):
        s = now_utc_iso()
        assert s.endswith("+00:00")
        assert "T" in s

    def test_no_microseconds(self):
        s = now_utc_iso()
        assert "." not in s


class TestEnsureDir:
    def test_creates_dir(self, tmp_path: Path):
        d = tmp_path / "a" / "b" / "c"
        ensure_dir(d)
        assert d.exists()
        assert d.is_dir()

    def test_idempotent(self, tmp_path: Path):
        d = tmp_path / "existing"
        d.mkdir()
        ensure_dir(d)
        assert d.exists()


class TestWriteJsonAtomic:
    def test_writes_valid_json(self, tmp_path: Path):
        f = tmp_path / "data.json"
        write_json_atomic(f, {"key": "value"})
        assert f.exists()
        import json
        data = json.loads(f.read_text())
        assert data == {"key": "value"}

    def test_atomic_replacement(self, tmp_path: Path):
        f = tmp_path / "data.json"
        f.write_text("old")
        write_json_atomic(f, {"new": "data"})
        import json
        data = json.loads(f.read_text())
        assert data == {"new": "data"}

    def test_no_tmp_leftover(self, tmp_path: Path):
        f = tmp_path / "data.json"
        write_json_atomic(f, {"key": "value"})
        assert not (tmp_path / "data.json.tmp").exists()


class TestRetryCall:
    def test_success_on_first_try(self):
        calls = []
        def action():
            calls.append(1)
            return "ok"
        result = retry_call("test", action, retries=2, backoff_seconds=0.1, retriable_exceptions=(RuntimeError,))
        assert result == "ok"
        assert len(calls) == 1

    def test_retry_then_success(self):
        calls = []
        def action():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("fail")
            return "ok"
        result = retry_call("test", action, retries=3, backoff_seconds=0.01, retriable_exceptions=(RuntimeError,))
        assert result == "ok"
        assert len(calls) == 3

    def test_exhausted_retries(self):
        def action():
            raise RuntimeError("fail")
        with pytest.raises(RuntimeError, match="failed after"):
            retry_call("test", action, retries=1, backoff_seconds=0.01, retriable_exceptions=(RuntimeError,))

    def test_non_retriable_exception(self):
        def action():
            raise ValueError("fail")
        with pytest.raises(ValueError):
            retry_call("test", action, retries=2, backoff_seconds=0.01, retriable_exceptions=(RuntimeError,))


class TestCheckMarkdownQuality:
    def test_good_quality(self, tmp_path: Path):
        f = tmp_path / "good.md"
        f.write_text("# Title\n\nThis is content.\n\nMore content here.\n")
        ok, reason = check_markdown_quality(f, min_chars=10, min_lines=2)
        assert ok
        assert reason == "ok"

    def test_too_few_chars(self, tmp_path: Path):
        f = tmp_path / "short.md"
        f.write_text("hi")
        ok, reason = check_markdown_quality(f, min_chars=100, min_lines=1)
        assert not ok
        assert "chars" in reason

    def test_too_few_lines(self, tmp_path: Path):
        f = tmp_path / "few_lines.md"
        f.write_text("a b c d e f g")
        ok, reason = check_markdown_quality(f, min_chars=1, min_lines=10)
        assert not ok
        assert "lines" in reason

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.md"
        f.write_text("")
        ok, reason = check_markdown_quality(f, min_chars=1, min_lines=1)
        assert not ok
