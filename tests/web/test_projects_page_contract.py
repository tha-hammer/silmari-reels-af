"""AF-4pz.6 — Projects page contract (mirrors test_index_contract style).

The page is a static artifact; its JS drives the AF-4pz.4/.5 APIs through the
named ``#config`` block only. These checks pin: the route serves it behind the
same auth-or-login gate as the index, the config block carries every API path
the JS uses, the key element ids exist, and no API path literal leaks into the
script body (named-config discipline).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import server
from conftest import FakeIdentity, make_ctx, make_deps
from deps import Unauthorized

PROJECTS_HTML = Path(__file__).resolve().parents[2] / "web" / "projects.html"

REQUIRED_API_PATHS = {
    "projectsPath": "/api/v1/projects",
    "projectPath": "/api/v1/projects/{id}",
    "projectAssetsPath": "/api/v1/projects/{id}/assets",
    "projectAssetPath": "/api/v1/projects/{id}/assets/{assetId}",
    "projectAssetDownloadPath": "/api/v1/projects/{id}/assets/{assetId}/download",
    "sourceAssetsPath": "/api/v1/source-assets",
    # AF-4pz.7: cut reels from a project video (reuse-source submit + poll).
    "executePath": "/api/v1/execute/async/{target}",
    "pollPath": "/api/v1/executions/{id}",
}
REQUIRED_ELEMENT_IDS = (
    "projectsList", "projectsEmpty", "newProjectName", "createProject",
    "assetsPanel", "projectTitle", "renameProject", "deleteProject",
    "addAssetForm", "assetType", "assetFile", "sourceAssetPicker", "assetUrl",
    "assetsList", "assetsEmpty", "uploadProgress", "uploadBar", "status",
    # AF-4pz.7 — the reels rail.
    "reelsList", "reelsEmpty",
)


def _client(deps):
    return server.create_app(deps, enable_supertokens=False).test_client()


def _config(html: str) -> dict:
    m = re.search(
        r'<script type="application/json" id="config">(.*?)</script>', html, re.DOTALL
    )
    assert m, "inline #config block not found"
    return json.loads(m.group(1))


def test_projects_route_serves_page_when_authenticated():
    deps = make_deps(identity=FakeIdentity(make_ctx("member")))
    resp = _client(deps).get("/projects")
    assert resp.status_code == 200
    assert b"PROJECTS" in resp.data


def test_projects_route_redirects_to_login_when_unauthenticated():
    deps = make_deps(identity=FakeIdentity(error=Unauthorized("no session")))
    resp = _client(deps).get("/projects")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")


def test_config_block_carries_every_api_path():
    cfg = _config(PROJECTS_HTML.read_text())
    assert cfg["api"] == REQUIRED_API_PATHS
    assert cfg["loginPath"] == "/login"


def test_key_element_ids_present():
    html = PROJECTS_HTML.read_text()
    for element_id in REQUIRED_ELEMENT_IDS:
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_no_api_literals_outside_config_block():
    """Named-config discipline: the JS body reads paths from CFG only."""
    html = PROJECTS_HTML.read_text()
    script_bodies = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    assert script_bodies, "page JS not found"
    for body in script_bodies:
        assert "/api/v1" not in body


def test_index_links_to_projects_page():
    index_html = (PROJECTS_HTML.parent / "index.html").read_text()
    assert 'href="/projects"' in index_html


def test_cut_reels_config_names_target_presets_and_poll_cadence():
    """AF-4pz.7: the roll flow is fully named-config driven — composite target,
    preset choices, and the poll cadence all live in the #config block."""
    cfg = _config(PROJECTS_HTML.read_text())
    reels = cfg["reels"]
    assert reels["compositeTarget"] == "reel-af.reel_composite_to_reel"
    assert "middle-third-dynamic" in reels["presets"]
    assert reels["pollIntervalMs"] > 0
    assert reels["maxPollMs"] > reels["pollIntervalMs"]


def test_download_links_only_from_download_url():
    """T10 discipline in the page JS: the reel link comes from download_url
    only — never result.url / local paths."""
    html = PROJECTS_HTML.read_text()
    script = re.search(r"<script>(.*?)</script>", html, re.DOTALL).group(1)
    assert "download_url" in script
    assert "result.url" not in script
