"""Regression: every @reel.reasoner must reach the served app / control-plane.

Guards the Slice-A ordering bug where ``app.include_router(reel)`` ran BEFORE
``dsl_hooks_to_reels`` was defined, so the reasoner was decorated onto the router
but never propagated into the served ``app`` — the agent registered 21 of 22
reasoners and the control plane answered dispatches with
``400 target 'reel_dsl_hooks_to_reels' not found on agent 'reel-af'``.

The router mount must stay at the BOTTOM of ``reel_af/app.py`` (after every
reasoner) for these to pass.
"""

from __future__ import annotations

import reel_af.app as app_mod


def _router_reasoner_ids() -> set[str]:
    """The ids the router SHOULD expose: ``<prefix>_<func name>`` for each
    reasoner decorated on ``reel`` (prefix is ``reel``)."""
    prefix = app_mod.reel.prefix
    return {f"{prefix}_{r['wrapper'].__name__}" for r in app_mod.reel.reasoners}


def test_every_reel_reasoner_is_registered_on_the_served_app():
    served = set(app_mod.app._reasoner_registry.keys())
    expected = _router_reasoner_ids()
    missing = expected - served
    assert not missing, (
        f"reasoners decorated on `reel` but NOT served by the app: {sorted(missing)} "
        "— app.include_router(reel) must run AFTER every @reel.reasoner is defined"
    )


def test_dsl_hooks_target_is_registered():
    # The A1 M2M target; absence is exactly the control-plane 'target not found' 400.
    assert "reel_dsl_hooks_to_reels" in app_mod.app._reasoner_registry


def test_router_and_served_app_reasoner_counts_match():
    assert len(app_mod.reel.reasoners) == len(app_mod.app._reasoner_registry)
