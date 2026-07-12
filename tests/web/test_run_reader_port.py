"""INT Phase 0 · Behavior 3 — research-run detail read via the OWNER interface.

The reader reaches deep-research run detail through the identity-free control-plane
client keyed by ``execution_id`` (API Composition), and fails closed — it never
synthesizes a row and never falls through to a local ``deepresearch.*`` SQL read.
"""

from __future__ import annotations

import pytest
from conftest import FakeControlPlane, make_ctx
from deps import BadGateway, NotFound, ResearchRunReaderPort, default_deps
from research_reader import OwnerInterfaceResearchRunReader


def test_reader_satisfies_port_and_reads_by_execution_id():
    cp = FakeControlPlane(response=(200, {"status": "succeeded", "title": "T"}, {}))
    reader = OwnerInterfaceResearchRunReader(cp)
    assert isinstance(reader, ResearchRunReaderPort)          # conforms to the port
    detail = reader.read(make_ctx(), "exec_1")
    assert detail["status"] == "succeeded"
    assert cp.get_execution_calls == ["exec_1"]               # went through the owner interface, by execution_id


def test_reader_fails_closed_when_owner_unreachable():
    cp = FakeControlPlane(get_error=BadGateway("owner down"))
    reader = OwnerInterfaceResearchRunReader(cp)
    with pytest.raises((NotFound, BadGateway)):               # NO local deepresearch.* fallback, no synthesized row
        reader.read(make_ctx(), "exec_1")


def test_reader_fails_closed_on_owner_404():
    cp = FakeControlPlane(response=(404, {"error": "not found"}, {}))
    reader = OwnerInterfaceResearchRunReader(cp)
    with pytest.raises(NotFound):
        reader.read(make_ctx(), "missing")


def test_reader_fails_closed_on_non_dict_body():
    cp = FakeControlPlane(response=(200, "not-a-dict", {}))
    reader = OwnerInterfaceResearchRunReader(cp)
    with pytest.raises(BadGateway):
        reader.read(make_ctx(), "exec_1")


def test_default_deps_wires_owner_interface_reader():
    deps = default_deps()                                     # no DB / network at build
    assert isinstance(deps.research_reader, ResearchRunReaderPort)
