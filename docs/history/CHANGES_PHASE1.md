# Phase 1: company (tenant) isolation

Naye / badle hue files
- app/core/tenancy.py            NAYA  - policy (co_<slug>__*, reg__*, deny by default)
- app/core/security.py           ClientInfo mein company; "collections=None => sab" khatam
- app/database/accounts.py       companies table, users.company_id (purana DB khud migrate), company_admin role,
                                 company disable => login + sessions band, online count
- app/api/deps.py                company sessions se, ensure_collection_manage, require_company_admin
- app/api/routes/rag.py          ingest / ingest-file / documents / delete ab can_manage se; /collections scope dikhata hai
- app/api/routes/admin.py        /admin/companies (CRUD), users mein company
- app/api/routes/company.py      NAYA  - company admin sirf apne users
- app/api/routes/auth.py         /auth/me aur login mein company + default_collection
- app/main.py                    company router
- scripts/create_company.py      NAYA
- scripts/create_user.py         --company, --role company_admin
- scripts/migrate_json_to_pgvector.py  NAYA (--dry-run, --map old=new)
- tests/test_tenancy.py          17 unit tests (pytest)
- tests/test_isolation_http.py   asli routes par do-company test

Chalane ke liye
1. python -m pytest tests -q
2. python scripts\create_user.py root --role admin     (agar admin pehle se hai to chhor do; purane admin chalte rahenge)
3. python scripts\create_company.py acme "Acme Surgical" --email info@acme.pk
4. python scripts\create_user.py boss_acme --role company_admin --company acme
5. python scripts\create_user.py ali_acme --company acme

# Phase 2 (super admin portal) - backend additions
- GET /api/v1/admin/overview?hours=24     super admin only: companies, online users, requests/errors/avg latency per company, GPU queue.
                                          Sirf ginti aur timing; sawal-jawab ya document ka content is mein kabhi nahi.
- UsageRepository.by_client()             SQLite + Postgres dono (Postgres wala aap ki machine par pehli baar chalega)
- AccountsRepository.list_online()
- tests: test_list_online_and_usage_by_client, test_overview_super_admin_only_and_no_content
