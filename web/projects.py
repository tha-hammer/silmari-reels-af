"""Projects domain model (AF-4pz.4/.5) — pure validation and view shaping.

A project is an org-scoped, owner-stamped container grouping media assets and
reels (root migration 115). project_asset attaches exactly one reference per
row: video (an existing ``source_asset`` or an upload), image/document
(upload → bucket key), link (validated http(s) URL). SQL lives in ``pg.py``;
routes in ``server.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from deps import BadRequest
from reel_jobs import _is_valid_url

PROJECT_NAME_MAX = 120
PROJECT_DESCRIPTION_MAX = 2000
ASSET_TITLE_MAX = 200
ASSET_TYPES = frozenset({"video", "image", "link", "document"})
UPLOAD_ASSET_TYPES = frozenset({"video", "image", "document"})


@dataclass(frozen=True)
class ProjectRef:
    project_id: Any
    org_id: Any
    created_by: Any
    name: str
    description: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ProjectAssetRef:
    asset_id: Any
    project_id: Any
    org_id: Any
    asset_type: str
    source_asset_id: Any | None = None
    bucket_key: str | None = None
    url: str | None = None
    title: str | None = None
    created_at: datetime | None = None


def validate_project_name(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise BadRequest("project name must be a non-empty string", code="invalid_project_name")
    return raw.strip()[:PROJECT_NAME_MAX]


def validate_project_description(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise BadRequest("project description must be a string", code="invalid_project_description")
    return raw.strip()[:PROJECT_DESCRIPTION_MAX] or None


def validate_asset_type(raw: Any) -> str:
    if not isinstance(raw, str) or raw.strip().lower() not in ASSET_TYPES:
        raise BadRequest(
            f"asset_type must be one of {sorted(ASSET_TYPES)}", code="invalid_asset_type"
        )
    return raw.strip().lower()


def validate_link_url(raw: Any) -> str:
    if not isinstance(raw, str) or not _is_valid_url(raw.strip()):
        raise BadRequest("link assets require a valid http(s) url", code="invalid_asset_url")
    return raw.strip()


def validate_asset_title(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise BadRequest("asset title must be a string", code="invalid_asset_title")
    return raw.strip()[:ASSET_TITLE_MAX] or None


def validate_source_asset_ref(raw: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise BadRequest(
            "source_asset_id must be a UUID", code="invalid_source_asset_id"
        ) from exc


def project_view(ref: ProjectRef) -> dict:
    return {
        "project_id": str(ref.project_id),
        "name": ref.name,
        "description": ref.description,
        "created_at": ref.created_at.isoformat() if ref.created_at else None,
        "updated_at": ref.updated_at.isoformat() if ref.updated_at else None,
    }


def project_asset_view(ref: ProjectAssetRef) -> dict:
    return {
        "asset_id": str(ref.asset_id),
        "project_id": str(ref.project_id),
        "asset_type": ref.asset_type,
        "source_asset_id": str(ref.source_asset_id) if ref.source_asset_id else None,
        "bucket_key": ref.bucket_key,
        "url": ref.url,
        "title": ref.title,
        "created_at": ref.created_at.isoformat() if ref.created_at else None,
    }
