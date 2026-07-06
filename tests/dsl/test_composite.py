"""B3: Read .ts.md into ordered segments and attachments — TDD tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from reel_af.dsl.ast import Extend, Insert, Join, Trans
from reel_af.dsl.composite import (
    CompositeDoc,
    CompositeSegment,
    MarkerAttachment,
    read_composite,
    read_composite_file,
)


# ── Fixture text (matches tests/dsl/fixtures/v1_supported.ts.md) ──

V1_FIXTURE = """\
00:00:04.120  They don't reason. They pattern-match at a scale that feels like reasoning. [extend tail 0.4]

[trans dissolve 0.8]

00:00:21.740  And the moment you trust the feeling, you ship the bug.
[trans ? 1.0 exclude=fadeblack => smoothleft]

[insert black 2.5]

00:01:12.300  So the fix isn't a smarter model. It's a tighter loop.
[extend head 0.5]

[join]

00:01:19.050  A loop you can actually see closing.
"""


class TestReadCompositeSegments:
    def test_four_segments(self):
        doc = read_composite(V1_FIXTURE)
        assert len(doc.segments) == 4

    def test_segment_indexes_ordered(self):
        doc = read_composite(V1_FIXTURE)
        assert [s.index for s in doc.segments] == [0, 1, 2, 3]

    def test_timecodes(self):
        doc = read_composite(V1_FIXTURE)
        assert doc.segments[0].timecode_s == pytest.approx(4.12)
        assert doc.segments[1].timecode_s == pytest.approx(21.74)
        assert doc.segments[2].timecode_s == pytest.approx(72.3)
        assert doc.segments[3].timecode_s == pytest.approx(79.05)

    def test_raw_text_strips_inline_marker(self):
        doc = read_composite(V1_FIXTURE)
        assert doc.segments[0].raw_text == (
            "They don't reason. They pattern-match at a scale that feels like reasoning."
        )

    def test_raw_text_no_inline_marker(self):
        doc = read_composite(V1_FIXTURE)
        assert doc.segments[1].raw_text == (
            "And the moment you trust the feeling, you ship the bug."
        )

    def test_normalized_text_collapses_whitespace(self):
        doc = read_composite("00:00:01.000   hello   world  \n")
        assert doc.segments[0].normalized_text == "hello world"


class TestReadCompositeMarkers:
    def test_six_markers_total(self):
        doc = read_composite(V1_FIXTURE)
        assert len(doc.markers) == 6

    def test_marker_0_extend_trailing_on_seg0(self):
        """[extend tail 0.4] inline on segment 0 -> segment_trailing."""
        doc = read_composite(V1_FIXTURE)
        m = doc.markers[0]
        assert m.locus == "segment_trailing"
        assert m.segment_index == 0
        assert isinstance(m.marker, Extend)
        assert m.marker.edge == "tail"

    def test_marker_1_trans_boundary_0_1(self):
        """[trans dissolve 0.8] after blank -> boundary between seg 0 and 1."""
        doc = read_composite(V1_FIXTURE)
        m = doc.markers[1]
        assert m.locus == "boundary"
        assert m.before_segment_index == 0
        assert m.after_segment_index == 1
        assert isinstance(m.marker, Trans)

    def test_marker_2_trans_trailing_on_seg1(self):
        """[trans ? 1.0 ...] immediately after seg 1 (no blank) -> segment_trailing."""
        doc = read_composite(V1_FIXTURE)
        m = doc.markers[2]
        assert m.locus == "segment_trailing"
        assert m.segment_index == 1
        assert isinstance(m.marker, Trans)

    def test_marker_3_insert_boundary_1_2(self):
        """[insert black 2.5] after blank -> boundary between seg 1 and 2."""
        doc = read_composite(V1_FIXTURE)
        m = doc.markers[3]
        assert m.locus == "boundary"
        assert m.before_segment_index == 1
        assert m.after_segment_index == 2
        assert isinstance(m.marker, Insert)

    def test_marker_4_extend_trailing_on_seg2(self):
        """[extend head 0.5] immediately after seg 2 (no blank) -> segment_trailing."""
        doc = read_composite(V1_FIXTURE)
        m = doc.markers[4]
        assert m.locus == "segment_trailing"
        assert m.segment_index == 2
        assert isinstance(m.marker, Extend)

    def test_marker_5_join_boundary_2_3(self):
        """[join] after blank -> boundary between seg 2 and 3."""
        doc = read_composite(V1_FIXTURE)
        m = doc.markers[5]
        assert m.locus == "boundary"
        assert m.before_segment_index == 2
        assert m.after_segment_index == 3
        assert isinstance(m.marker, Join)


class TestTrailingMarkersOnSegments:
    def test_seg0_has_one_trailing(self):
        doc = read_composite(V1_FIXTURE)
        assert len(doc.segments[0].trailing_markers) == 1
        assert isinstance(doc.segments[0].trailing_markers[0].marker, Extend)

    def test_seg1_has_one_trailing(self):
        doc = read_composite(V1_FIXTURE)
        assert len(doc.segments[1].trailing_markers) == 1
        assert isinstance(doc.segments[1].trailing_markers[0].marker, Trans)

    def test_seg2_has_one_trailing(self):
        doc = read_composite(V1_FIXTURE)
        assert len(doc.segments[2].trailing_markers) == 1
        assert isinstance(doc.segments[2].trailing_markers[0].marker, Extend)

    def test_seg3_has_no_trailing(self):
        doc = read_composite(V1_FIXTURE)
        assert len(doc.segments[3].trailing_markers) == 0


class TestSourceLocus:
    def test_source_path_propagated(self):
        doc = read_composite(V1_FIXTURE, source_path=Path("test.ts.md"))
        assert doc.source_path == Path("test.ts.md")
        for seg in doc.segments:
            assert seg.source.path == Path("test.ts.md")

    def test_segment_line_numbers(self):
        doc = read_composite(V1_FIXTURE)
        assert doc.segments[0].source.line == 1
        assert doc.segments[1].source.line == 5
        assert doc.segments[2].source.line == 10
        assert doc.segments[3].source.line == 15

    def test_marker_line_numbers(self):
        doc = read_composite(V1_FIXTURE)
        assert doc.markers[0].source.line == 1   # inline on seg 0
        assert doc.markers[1].source.line == 3   # [trans dissolve 0.8]
        assert doc.markers[2].source.line == 6   # [trans ? ...]
        assert doc.markers[3].source.line == 8   # [insert black 2.5]
        assert doc.markers[4].source.line == 11  # [extend head 0.5]
        assert doc.markers[5].source.line == 13  # [join]


class TestReadCompositeFile:
    def test_file_reader(self, fixture_path):
        path = fixture_path("v1_supported.ts.md")
        doc = read_composite_file(path)
        assert doc.source_path == path
        assert len(doc.segments) == 4
        assert len(doc.markers) == 6


class TestEdgeCases:
    def test_empty_text(self):
        doc = read_composite("")
        assert len(doc.segments) == 0
        assert len(doc.markers) == 0

    def test_blank_lines_only(self):
        doc = read_composite("\n\n\n")
        assert len(doc.segments) == 0

    def test_marker_before_any_segment(self):
        text = "[trans dissolve 0.5]\n00:00:01.000  Hello world\n"
        doc = read_composite(text)
        assert len(doc.markers) == 1
        assert doc.markers[0].locus == "point"
        assert doc.markers[0].after_segment_index == 0

    def test_marker_after_last_segment(self):
        text = "00:00:01.000  Hello world\n\n[join]\n"
        doc = read_composite(text)
        assert len(doc.markers) == 1
        m = doc.markers[0]
        assert m.locus == "boundary" or m.locus == "point"
        assert m.before_segment_index == 0

    def test_multiple_trailing_markers(self):
        text = (
            "00:00:01.000  Hello world\n"
            "[extend tail 0.5]\n"
            "[trans dissolve 0.8]\n"
            "\n"
            "00:00:05.000  Second segment\n"
        )
        doc = read_composite(text)
        assert len(doc.segments[0].trailing_markers) == 2
        assert doc.segments[0].trailing_markers[0].locus == "segment_trailing"
        assert doc.segments[0].trailing_markers[1].locus == "segment_trailing"

    def test_leading_trailing_whitespace(self):
        text = "  00:00:01.000  Hello world  \n"
        doc = read_composite(text)
        assert len(doc.segments) == 1
        assert doc.segments[0].raw_text == "Hello world"

    def test_schema_and_dsl_version(self):
        doc = read_composite("")
        assert doc.schema_version == "1"
        assert doc.dsl_version == "2"
