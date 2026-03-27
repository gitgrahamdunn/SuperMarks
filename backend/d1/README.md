D1 migration scaffold

Files:
- `d1/migrations/0001_initial.sql`: SQLite-compatible schema generated from current SQLModel metadata
- `app/repositories/d1_provider.py`: backend selection seam for the live D1 bridge-backed repository implementation

Current status:
- repository extraction is broad enough to support a D1-backed implementation
- initial schema artifact exists
- Worker-side D1 HTTP bridge exists in `cloudflare/index.js`
- Python-side D1 bridge client exists in `app/d1_bridge.py`
- hosted runtime now defaults to `SUPERMARKS_REPOSITORY_BACKEND=d1-bridge`
- `questions`, `exams`, `submissions`, and `reporting` all have bridge-backed repository implementations
- local/test runs can still fall back to SQLModel execution when bridge env is not configured

Current shape:
- the hosted backend target uses a Cloudflare Worker wrapper plus a Python container
- D1 bindings attach to the Worker environment, not directly to the container process
- this is now handled by the Worker-side bridge route `/_supermarks/d1/*`

What remains:
1. finish hosted deployment once Cloudflare Containers access is available
2. broaden live D1 integration coverage beyond repository-layer smoke checks
3. remove SQLModel fallback paths if you want `d1-bridge` to be mandatory everywhere it is selected

Migration commands once a D1 database exists:
```bash
cd /home/graham/repos/SuperMarks/backend
wrangler d1 migrations apply <database-name> --local
wrangler d1 migrations apply <database-name> --remote
```

Wrangler binding shape:
```toml
[[d1_databases]]
binding = "SUPERMARKS_DB"
database_name = "<database-name>"
database_id = "<uuid>"
preview_database_id = "<uuid>"
migrations_dir = "d1/migrations"
```

Bridge env:
```env
SUPERMARKS_REPOSITORY_BACKEND=d1-bridge
SUPERMARKS_D1_BRIDGE_URL=https://<backend-domain>/_supermarks/d1
SUPERMARKS_D1_BRIDGE_TOKEN=<internal-token>
```

Bridge endpoints:
- `POST /_supermarks/d1/health`
- `POST /_supermarks/d1/query`
- `POST /_supermarks/d1/run`
- `POST /_supermarks/d1/batch`
- `POST /_supermarks/d1/exec`

Until that runtime decision is made, keep using:
- local SQLite for local-only workflows, or
- the hosted D1 bridge path for Cloudflare deployment
