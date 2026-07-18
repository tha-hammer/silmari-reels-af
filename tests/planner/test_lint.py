from __future__ import annotations

from reel_af.dsl.models import DslWord, WordsSidecar
from reel_af.planner.config import PlannerConfig
from reel_af.planner.lint import lint_blueprint


def _cfg(**overrides) -> PlannerConfig:
    data = {
        "model": "test-model",
        "default_register": "educational",
        "max_repair_passes": 1,
        "r1_hook_window_s": 3.5,
        "r2_cadence_s": {"entertainment": 3.0, "educational": 5.0, "b2b": 9.0},
        "r4_max_gap_s": 0.6,
        "r8_min_token_overlap": 0.5,
        "r11_bait_patterns": [
            r"\bcomment\s+[a-z0-9_#-]+\b",
            r"\btag\s+\d+\b",
            r"\blike\s+if\b",
            r"\bcomment\s+\w+\s+for\b",
        ],
    }
    data.update(overrides)
    return PlannerConfig.model_validate(data)


def _blueprint(**overrides):
    bp = {
        "hook": {
            "banner_line": "send this to a dev who ships on Friday",
            "span_quote": "alpha beta",
        },
        "beats": [
            {
                "role": "hook",
                "span_quote": "alpha beta",
                "start_s": 1.0,
                "end_s": 2.0,
            },
            {
                "role": "value",
                "span_quote": "gamma delta",
                "start_s": 2.0,
                "end_s": 3.0,
            },
        ],
        "loop": {"final_span_quote": "alpha beta"},
        "cta": {"placements": ["end"]},
    }
    bp.update(overrides)
    return bp


def test_engagement_bait_hard_fails():
    bp = _blueprint(hook={"banner_line": "comment YES if you agree", "span_quote": "alpha beta"})

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R11" and d.severity == "error" for d in diags)


def test_clean_cta_passes_r11():
    diags = lint_blueprint(_blueprint(), words=None, cfg=_cfg())

    assert not any(d.rule == "R11" for d in diags)


def test_hook_window_over_threshold_warns():
    bp = _blueprint(
        beats=[
            {"role": "hook", "span_quote": "alpha", "duration_s": 2.0},
            {"role": "hook", "span_quote": "beta", "duration_s": 2.5},
            {"role": "value", "span_quote": "gamma", "duration_s": 1.0},
        ]
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R1" and d.severity == "warning" for d in diags)


def test_long_segment_without_change_warns():
    bp = _blueprint(
        beats=[
            {
                "role": "value",
                "span_quote": "alpha beta",
                "duration_s": 9.0,
                "interrupt_out": None,
                "cutin": None,
            }
        ]
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg(), register="entertainment")

    assert any(d.rule == "R2" and d.severity == "warning" for d in diags)


def test_internal_dead_air_warns():
    words = WordsSidecar(
        words=[
            DslWord(w="a", start=1.0, end=1.2),
            DslWord(w="b", start=2.1, end=2.3),
        ]
    )
    bp = _blueprint(beats=[{"role": "value", "span_quote": "a b", "start_s": 1.0, "end_s": 2.3}])

    diags = lint_blueprint(bp, words=words, cfg=_cfg())

    assert any(d.rule == "R4" and d.severity == "warning" for d in diags)


def test_final_not_echoing_hook_warns():
    bp = _blueprint(
        hook={"banner_line": "clean", "span_quote": "alpha beta"},
        loop={"final_span_quote": "zeta omega"},
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R8" and d.severity == "warning" for d in diags)


def test_non_decreasing_back_half_warns():
    bp = _blueprint(
        beats=[
            {"role": "hook", "span_quote": "a", "duration_s": 2.0},
            {"role": "context", "span_quote": "b", "duration_s": 3.0},
            {"role": "value", "span_quote": "c", "duration_s": 4.0},
        ]
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R3" and d.severity == "warning" for d in diags)


def test_strictly_decreasing_back_half_passes_r3():
    bp = _blueprint(
        beats=[
            {"role": "hook", "span_quote": "a", "duration_s": 5.0},
            {"role": "context", "span_quote": "b", "duration_s": 4.0},
            {"role": "value", "span_quote": "c", "duration_s": 3.0},
            {"role": "payoff", "span_quote": "a", "duration_s": 2.0},
        ]
    )

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert not any(d.rule == "R3" for d in diags)


def test_multiple_primary_ctas_warn():
    bp = _blueprint(cta={"placements": ["middle", "end"]})

    diags = lint_blueprint(bp, words=None, cfg=_cfg())

    assert any(d.rule == "R12" and d.severity == "warning" for d in diags)
