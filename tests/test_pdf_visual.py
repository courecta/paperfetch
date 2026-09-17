"""Float crop targeting: exact from bounding boxes, approximate from captions."""

from __future__ import annotations

from paperfetch_app.extract.ir import Block, Document, Figure, Inline, Table
from paperfetch_app.pdf_visual import _band_around_caption, _caption_targets, _crop_margins, _float_targets

PAGE_H = 800.0


def _doc(blocks: list[Block]) -> Document:
    return Document(schema_version=1, key="k", source_kind="x", source_url="u", blocks=blocks)


class TestTargetSelection:
    def test_blocks_with_a_bbox_are_cropped_exactly(self):
        block = Block(kind="figure", id="f1", figure=Figure(id="f1"), meta={"bbox": [1, 2, 3, 4], "page": 2})
        assert _float_targets(_doc([block])) == [("f1", "figure", [1.0, 2.0, 3.0, 4.0], 2)]
        # ...and are not also searched for by caption.
        assert _caption_targets(_doc([block])) == []

    def test_blocks_without_a_bbox_fall_back_to_the_label(self):
        block = Block(kind="table", id="t1", table=Table(id="t1", label="Table 3:"))
        assert _caption_targets(_doc([block])) == [("t1", "table", "Table 3")]

    def test_caption_text_is_used_when_there_is_no_label(self):
        caption = [Inline(kind="text", text="The overall architecture of our proposed framework for detection")]
        block = Block(kind="figure", id="f2", figure=Figure(id="f2", caption=caption))
        targets = _caption_targets(_doc([block]))
        assert len(targets) == 1
        needle = targets[0][2]
        assert needle and len(needle) <= 40 and caption[0].text.startswith(needle)

    def test_float_with_no_label_or_caption_is_skipped(self):
        block = Block(kind="figure", id="f3", figure=Figure(id="f3"))
        assert _caption_targets(_doc([block])) == []


class TestCropGeometry:
    def test_figures_extend_above_their_caption(self):
        # Margins are (left, bottom, right, top) from each page edge, so the
        # band's upper edge is height - top_margin.
        _, bottom_margin, _, top_margin = _band_around_caption((100.0, 400.0, 400.0, 410.0), "figure", PAGE_H)
        upper_edge = PAGE_H - top_margin
        assert 350.0 < bottom_margin < 400.0  # starts one margin below the caption
        assert upper_edge > 700.0  # and reaches far above it

    def test_tables_extend_below_their_caption(self):
        _, bottom_margin, _, top_margin = _band_around_caption((100.0, 400.0, 400.0, 410.0), "table", PAGE_H)
        upper_edge = PAGE_H - top_margin
        assert bottom_margin < 100.0  # reaches far below the caption
        assert 410.0 < upper_edge < 450.0  # and stops just above it

    def test_band_is_clamped_to_the_page(self):
        left, bottom, right, top = _band_around_caption((0.0, 5.0, 10.0, 795.0), "figure", PAGE_H)
        assert all(v >= 0.0 for v in (left, bottom, right, top))

    def test_degenerate_bbox_yields_no_crop(self):
        assert _crop_margins([10.0, 10.0, 10.2, 10.2], 600.0, PAGE_H) is None

    def test_bbox_converts_from_top_left_origin(self):
        # A box at the very top of the page should leave no top margin.
        margins = _crop_margins([0.0, 0.0, 600.0, 100.0], 600.0, PAGE_H)
        assert margins == (0.0, PAGE_H - 100.0, 0.0, 0.0)
