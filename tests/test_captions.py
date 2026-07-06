"""B2 caption timings + B3 caption ASS (safe zone, grouped).

Copies the proven whisper-on-final-reel + grouped-ASS approach from the
delivered ``enhance_reel.py`` driver and productionises it behind a config.
The pure pieces (word parsing, phrase grouping, ASS emission) are fully
deterministic and unit-tested; the whisper subprocess is injected so the
real ffprobe/wav path runs under ffmpeg without needing a speech fixture.
"""

from __future__ import annotations

from util_captions import (
    StubFinishConfig,
    fake_whisper_json,
    make_silent_reel,
    parse_dialogues,
    requires_ffmpeg,
)

from reel_af.render import captions

# ───── B2: caption timings from the final reel ───────────────────────


def test_parse_whisper_words_strips_clamps_and_orders():
    data = fake_whisper_json(
        [(0.0, 0.4, "hello"), (0.4, 0.9, "there"), (0.9, 1.5, "world")]
    )
    words = captions._parse_whisper_words(data, duration=2.0)
    assert [w[2] for w in words] == ["hello", "there", "world"]
    assert all(0.0 <= s <= e <= 2.0 for s, e, _ in words)


def test_parse_whisper_words_clamps_overrun_to_duration():
    data = fake_whisper_json([(0.0, 0.5, "a"), (0.5, 9.9, "b")])
    words = captions._parse_whisper_words(data, duration=1.0)
    assert words[-1][1] == 1.0  # end clamped to reel duration


def test_parse_whisper_words_drops_empty_tokens():
    data = fake_whisper_json([(0.0, 0.3, "keep"), (0.3, 0.6, "   ")])
    words = captions._parse_whisper_words(data, duration=1.0)
    assert [w[2] for w in words] == ["keep"]


@requires_ffmpeg
def test_caption_words_uses_real_duration_and_injected_transcriber(tmp_path):
    reel = make_silent_reel(tmp_path / "reel.mp4", seconds=2.0)

    seen: dict = {}

    def fake_transcribe(path, *, model, workdir):
        seen["path"] = path
        seen["model"] = model
        # a word intentionally overruns the 2s reel to prove clamping
        return fake_whisper_json([(0.1, 0.6, "crisp"), (0.6, 5.0, "reel")])

    words = captions.caption_words(reel, transcribe=fake_transcribe)

    assert seen["path"] == reel
    assert [w[2] for w in words] == ["crisp", "reel"]
    dur = captions._reel_duration(reel)
    assert 1.8 <= dur <= 2.3  # ffmpeg rounds container duration a touch
    assert all(0.0 <= s <= e <= dur + 0.05 for s, e, _ in words)


# ───── B3: phrase grouping ───────────────────────────────────────────


def test_group_captions_respects_max_words():
    cfg = StubFinishConfig(caption_max_words=4, caption_max_dur_s=99, caption_gap_s=99)
    words = [(i * 0.2, i * 0.2 + 0.15, f"w{i}") for i in range(10)]
    groups = captions.group_captions(words, cfg)
    assert all(len(txt.split()) <= 4 for _, _, txt in groups)
    assert sum(len(txt.split()) for _, _, txt in groups) == 10


def test_group_captions_splits_on_gap():
    cfg = StubFinishConfig(caption_max_words=99, caption_max_dur_s=99, caption_gap_s=0.35)
    words = [(0.0, 0.3, "a"), (0.4, 0.7, "b"), (1.5, 1.8, "c")]  # 0.8s gap before c
    groups = captions.group_captions(words, cfg)
    assert [txt for _, _, txt in groups] == ["a b", "c"]


def test_group_captions_splits_on_duration():
    cfg = StubFinishConfig(caption_max_words=99, caption_max_dur_s=1.0, caption_gap_s=99)
    words = [(0.0, 0.3, "a"), (0.5, 0.8, "b"), (1.4, 1.7, "c")]  # a..c spans 1.7s
    groups = captions.group_captions(words, cfg)
    # first group closes once adding a word would exceed 1.0s span
    assert groups[0][2] == "a b"
    assert groups[0][1] - groups[0][0] <= 1.0 or len(groups[0][2].split()) == 1


def test_group_captions_start_before_end_and_ordered():
    cfg = StubFinishConfig()
    words = [(i * 0.3, i * 0.3 + 0.2, f"w{i}") for i in range(12)]
    groups = captions.group_captions(words, cfg)
    starts = [g[0] for g in groups]
    assert starts == sorted(starts)
    assert all(s < e for s, e, _ in groups)


# ───── B3: caption ASS emission ──────────────────────────────────────


def _caption_dialogues(ass_text: str):
    return [d for d in parse_dialogues(ass_text) if d.style == captions.CAPTION_STYLE_NAME]


def test_build_caption_ass_positions_at_safe_zone():
    cfg = StubFinishConfig(caption_safe_y=1330, center_x=540)
    words = [(0.0, 0.3, "hello"), (0.3, 0.6, "world")]
    ass = captions.build_caption_ass(words, cfg)
    dials = _caption_dialogues(ass)
    assert dials, "expected at least one caption dialogue"
    assert all(d.y == 1330 and d.x == 540 for d in dials)


def test_build_caption_ass_safe_y_override_moves_pos():
    words = [(0.0, 0.3, "hello"), (0.3, 0.6, "world")]
    low = _caption_dialogues(captions.build_caption_ass(words, StubFinishConfig(caption_safe_y=1330)))
    high = _caption_dialogues(captions.build_caption_ass(words, StubFinishConfig(caption_safe_y=900)))
    assert low[0].y == 1330
    assert high[0].y == 900


def test_build_caption_ass_uppercases_by_default():
    cfg = StubFinishConfig(caption_uppercase=True)
    ass = captions.build_caption_ass([(0.0, 0.3, "hello"), (0.3, 0.6, "there")], cfg)
    assert _caption_dialogues(ass)[0].text.strip() == "HELLO THERE"


def test_build_caption_ass_respects_lowercase_opt_out():
    cfg = StubFinishConfig(caption_uppercase=False)
    ass = captions.build_caption_ass([(0.0, 0.3, "Hello")], cfg)
    assert _caption_dialogues(ass)[0].text.strip() == "Hello"


def test_build_caption_ass_events_ordered_and_positive_span():
    cfg = StubFinishConfig()
    words = [(i * 0.25, i * 0.25 + 0.2, f"w{i}") for i in range(16)]
    dials = _caption_dialogues(captions.build_caption_ass(words, cfg))
    assert all(d.start < d.end for d in dials)
    assert [d.start for d in dials] == sorted(d.start for d in dials)


def test_build_caption_ass_declares_playres_and_style():
    cfg = StubFinishConfig(canvas_w=1080, canvas_h=1920)
    ass = captions.build_caption_ass([(0.0, 0.3, "x")], cfg)
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert f"Style: {captions.CAPTION_STYLE_NAME}," in ass


def test_build_caption_ass_escapes_braces():
    cfg = StubFinishConfig(caption_uppercase=False)
    ass = captions.build_caption_ass([(0.0, 0.3, "a{b}c")], cfg)
    # raw braces in payload would corrupt the ASS override parser
    dials = _caption_dialogues(ass)
    assert "{" not in dials[0].text and "}" not in dials[0].text


def test_empty_words_yields_no_caption_dialogues():
    ass = captions.build_caption_ass([], StubFinishConfig())
    assert _caption_dialogues(ass) == []


def test_write_ass_round_trips(tmp_path):
    ass = captions.build_caption_ass([(0.0, 0.3, "hi")], StubFinishConfig())
    out = captions.write_ass(ass, tmp_path / "sub" / "cap.ass")
    assert out.exists()
    assert out.read_text() == ass


# ───── B3: alignment against the REAL ReelFinishConfig (B0) ───────────


def test_builders_read_the_real_reel_finish_config():
    from reel_af.render.finish_config import ReelFinishConfig, caption_pos_tag

    cfg = ReelFinishConfig()
    ass = captions.build_caption_ass([(0.0, 0.3, "hi"), (0.3, 0.6, "there")], cfg)
    dials = _caption_dialogues(ass)
    assert dials and all(d.y == cfg.caption_safe_y and d.x == cfg.center_x for d in dials)
    # the config's own pos helper must agree with what we emit
    assert caption_pos_tag(cfg) in ass


def test_real_config_overrides_flow_through():
    from reel_af.render.finish_config import AssStyle, ReelFinishConfig

    cfg = ReelFinishConfig(
        caption_safe_y=1000,
        caption_uppercase=False,
        caption_style=AssStyle(fontsize=72, outline_colour="&H00FF00FF"),
    )
    ass = captions.build_caption_ass([(0.0, 0.3, "Keep")], cfg)
    dials = _caption_dialogues(ass)
    assert dials[0].y == 1000
    assert dials[0].text.strip() == "Keep"
    style_line = next(ln for ln in ass.splitlines() if ln.startswith(f"Style: {captions.CAPTION_STYLE_NAME},"))
    assert "&H00FF00FF" in style_line  # British-spelled outline_colour flows through
    assert style_line.split(",")[2] == "72"  # fontsize override
