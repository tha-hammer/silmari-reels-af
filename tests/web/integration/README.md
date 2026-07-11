# Web Integration Tests

These tests exercise the real Postgres-backed repository adapters and the real object
storage adapter smoke path. They are intentionally excluded from default infrastructure
requirements:

- Set `TEST_DATABASE_URL` to a live Postgres URL before running database integration
  tests: `TEST_DATABASE_URL=postgres://... uv run pytest -q -m integration`.
- Set `REEL_BUCKET_NAME` plus the normal object-storage credentials before running the
  storage smoke test.
- When those variables are absent, the tests skip with an explicit reason. That is a
  fail-closed signal that the live closure is unverified in the current environment, not
  a proof that the production schema or bucket works.

## Required Deepresearch Schema

This repository does not vendor the root-owned `migrations/deepresearch/` files. The
integration fixtures below mirror the minimum production schema that PR #9 requires, and
`web.pg.REQUIRED_SCHEMA` is the runtime readiness gate that returns 503 when required
tables or columns are missing.

Required tables and columns:

- `organization`: `id`, `slug`, `name`, `status`
- `user`: `id`, `supertokens_user_id`, `email`, `status`
- `membership`: `org_id`, `user_id`, `role`, `status`
- `role_definition`: `role`, `permissions`
- `research_run`: `id`, `org_id`, `created_by`, `execution_id`, `status`, `created_at`
- `reel_job`: `id`, `org_id`, `created_by`, `client_request_id`, `title`,
  `source_url`, `topic`, `source_research_run_id`, `params`, `status`, `result_ref`,
  `execution_id`, `created_at`, `completed_at`
- `carousel`: `id`, `org_id`, `created_by`, `client_request_id`, `status`,
  `source_research_run_id`, `hq_recreate_count`, `execution_id`, `created_at`
- `carousel_slide`: `carousel_id`, `org_id`, `idx`, `image_ref`, `prompt`, `status`

Required uniqueness and foreign-key behavior covered by these tests:

- `reel_job` and `carousel` are idempotent on `(org_id, created_by, client_request_id)`.
- `reel_job.source_research_run_id` and `carousel.source_research_run_id` reference
  `research_run(id)` and use `on delete set null`.
- `carousel_slide.carousel_id` references `carousel(id)` and cascades on delete.
- Repository reads and writes are org-scoped; foreign rows are concealed as 404/`NotFound`.
- `carousel.hq_recreate_count` is the durable, atomic HQ recreate cap counter.
