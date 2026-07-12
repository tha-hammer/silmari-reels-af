-- ============================================================================
-- INT Phase 0 · Behavior 1 — Prod DDL audit for deepresearch.research_run
-- ============================================================================
-- READ-ONLY diagnostic. Determines the TRUE prod state of the shared
-- `deepresearch.research_run` table (which table definition won, row count,
-- duplicate rows per execution_id) as the decision input Phase 1 consumes.
--
-- Plan: thoughts/searchable/shared/plans/2026-07-12-tdd-int-00-stop-cross-write.md (Behavior 1)
--
-- SAFETY (ISC-9): this file is read-only by construction — only SELECT queries
--   and psql \d / system-catalog introspection. It performs no schema mutation
--   and no data mutation of any kind. A human operator runs it against the prod
--   `deepresearch` DB; CI never executes it.
--   Read-only gate: a case-insensitive scan for the six data/schema-mutation
--   keywords over this file must return zero lines.
--
-- HOW TO RUN (operator, against prod deepresearch DB):
--   psql "$DEEPRESEARCH_DATABASE_URL" -f research_run_prod_audit.sql
-- Record each result into the "Handoff to Phase 1" note in the plan.
-- ============================================================================

-- 1. WHICH TABLE DEFINITION WON — inspect the live shape.
--    Decision branch:
--      has run_id + query + visibility + params + status CHECK  -> OWNER shape won (schema.sql:7-23)
--      only the thin columns (id, org_id, creator, execution_id, status, ts) -> reel-af 109 shape won (109_...:9-19)
\d deepresearch.research_run

-- 2. ROW COUNT (blast radius for any Phase 1 migration/backfill).
select count(*) as total_rows
from deepresearch.research_run;

-- 3. DUPLICATES PER execution_id (the intended join key; UNIQUE on neither shape).
--    n > 1 confirms the "two rows per logical run" hazard Phase 1 must collapse
--    on execution_id BEFORE it can add UNIQUE(execution_id).
select execution_id, count(*) as n
from deepresearch.research_run
where execution_id is not null
group by execution_id
having count(*) > 1
order by n desc;

-- 4. NULL execution_id rows (cannot be joined to reel-af's source_research_run_id;
--    report separately — these need a distinct Phase 1 remediation decision).
select count(*) as null_execution_id_rows
from deepresearch.research_run
where execution_id is null;

-- 5. (context) Constraint set actually enforced on prod (confirms winning shape's CHECKs/uniques).
select conname, contype
from pg_constraint
where conrelid = 'deepresearch.research_run'::regclass
order by contype, conname;
