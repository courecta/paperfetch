from __future__ import annotations

import sys
from pathlib import Path
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperfetch_app.manifests import collect_inputs, parse_legacy_python


class ManifestTests(unittest.TestCase):
    def test_collect_inputs_dedupes_by_identity(self) -> None:
        papers = collect_inputs(
            manifests=[],
            from_python=[],
            inline_specs=[],
            urls=[
                "https://arxiv.org/abs/2411.00278",
                "https://arxiv.org/pdf/2411.00278.pdf",
            ],
        )
        self.assertEqual(len(papers), 1)

    def test_parse_legacy_annassign(self) -> None:
        code = textwrap.dedent(
            """
            from dataclasses import dataclass
            @dataclass
            class Paper:
                slug: str
                name: str
                url: str

            PAPERS: list[Paper] = [
                Paper("a", "A", "https://arxiv.org/abs/1234.56789"),
            ]
            """
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "legacy.py"
            path.write_text(code, encoding="utf-8")
            parsed = parse_legacy_python(path)
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0].slug, "a")


if __name__ == "__main__":
    unittest.main()
