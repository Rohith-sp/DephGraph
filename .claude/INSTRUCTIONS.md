## Code Style

### Python (FastAPI)
- Formatter: Black (line-length=100)
- Imports: isort with profile=black
- Type hints: everywhere including return types
- No bare `except:` — always catch specific exceptions
- Log with structlog, NOT print()

### TypeScript (Next.js)
- Prettier + ESLint next/core-web-vitals
- Prefer const over let; no var
- No `any` — use `unknown` + type narrowing

### SQL
- UPPERCASE keywords
- snake_case for all identifiers
- Always alias tables in multi-table queries

## Database Conventions

### PostgreSQL (Supabase)
- IDs: BIGSERIAL (not UUID — too slow for joins)
- Timestamps: TIMESTAMPTZ always
- Hard delete with ON DELETE CASCADE (no soft deletes)
- RLS: every new table needs ENABLE ROW LEVEL SECURITY + select policy
- New M:N: always a join table, never arrays
- Migrations: numbered, never edit existing

### MongoDB
- ObjectId → str before JSON serialization
- TTL indexes: runtime_samples (90d), review_responses (30d) — already set
- Never store sensitive data here (no RLS)

### Redis
- Key pattern: `dephgraph:{resource}:{id}`
- TTL: blast_radius = 86400s, prompts = 604800s
- Use nx=True for set operations to avoid race conditions

## Testing
- Every router endpoint: happy path + 404 + 422 = minimum 3 tests
- Use pytest-asyncio with @pytest.mark.asyncio
- Mock all external HTTP calls (OSV.dev, deps.dev, Groq) with respx
- Coverage target: ≥70%

## Common Pitfalls
- asyncpg uses $1 $2 placeholders, NOT %s (that's psycopg2)
- Supabase RLS blocks ALL by default — add policies for new tables
- ReactFlow needs <ReactFlowProvider> wrapping — can't use hooks outside it
- Render free tier has NO persistent disk — don't write files in production
- Groq free: 30 req/min — add exponential backoff with jitter
- SBERT model (80MB) downloads on first run — download at startup, not per-request

## Stage 2 PR Gate Decision Logic
- BLOCK if: CVSS ≥ 9.0, OR copyleft license, OR abandonment_score ≥ 80
- ADVISORY if: CVSS 6.0–8.9, OR abandonment 40–79, OR vuln with no fixed_in
- PASS if: no vulns, OR all CVSS < 6.0 with a fix available
