"""AF-4pz.3 — project + project_asset schema surface (unit).

Schema-only bead: the FEATURE_SCHEMA fail-closed surface documents the tables
the root migration 115 provides. Repo methods land with the CRUD beads
(AF-4pz.4/.5); the SQL constraint contract is proven by the integration tests.
"""

from __future__ import annotations

from pg import FEATURE_SCHEMA


def test_feature_schema_includes_project_columns():
    assert FEATURE_SCHEMA["project"] >= {
        "id", "org_id", "created_by", "name", "description",
        "created_at", "updated_at", "deleted_at",
    }


def test_feature_schema_includes_project_asset_columns():
    assert FEATURE_SCHEMA["project_asset"] >= {
        "id", "project_id", "org_id", "asset_type", "source_asset_id",
        "bucket_key", "url", "title", "created_at", "deleted_at",
    }
