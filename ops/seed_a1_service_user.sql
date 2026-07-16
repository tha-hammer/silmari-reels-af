-- Seed the A1 service member in the shared `deepresearch` Postgres.
--
-- Root/ops-owned: reel-af only READS the `deepresearch` schema (web/pg.py
-- REQUIRED_SCHEMA gate), so this seed is applied by the deep-research migration
-- process or a one-off `psql` — NOT a reel-af migration. Idempotent.
--
-- Identity contract (must match web/auth.py):
--   supertokens_user_id = 'svc:a1-pipeline'
--   email               = 'a1-pipeline+service@silmari.ai'
--   role                = 'member'   (least-privilege role that can submit AND poll)
--   default org         = 'e4e47131-cd9f-4882-9925-194e9db062ca' (REEL_DEFAULT_ORG_ID)
--
-- The `<uuid>` below is a fresh app-user id; generate one (e.g. gen_random_uuid())
-- or substitute a fixed value per environment. The membership references it.

insert into deepresearch."user"(id, supertokens_user_id, email, status)
  values (gen_random_uuid(), 'svc:a1-pipeline', 'a1-pipeline+service@silmari.ai', 'active')
  on conflict (supertokens_user_id) do nothing;

insert into deepresearch.membership(org_id, user_id, role, status)
  select 'e4e47131-cd9f-4882-9925-194e9db062ca', u.id, 'member', 'active'
    from deepresearch."user" u
   where u.supertokens_user_id = 'svc:a1-pipeline'
  on conflict (org_id, user_id) do nothing;
