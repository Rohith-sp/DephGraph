#!/usr/bin/env bash
# Runs the docs/DEMO.md walkthrough in order: setup, A (MySQL course work), B (local app), C (live site).
# Before each step it prints what to say and waits for Enter, so it can be presented as is.
#
#   scripts/demo.sh                  # everything, pausing before each step
#   scripts/demo.sh A C              # only some parts (setup always runs first)
#   scripts/demo.sh --no-pause       # dry run: no waiting
#   scripts/demo.sh --first-time     # fresh Docker volume: also load the extra demo data and clusters
#
# The Groq key for the PR gate's LLM stage is read from GROQ_API_KEY or .env.local; it is never printed.
set -euo pipefail
cd "$(dirname "$0")/.."

PAUSE=1 FIRST=0 PARTS=()
for a in "$@"; do
  case $a in
    --no-pause) PAUSE=0 ;;
    --first-time) FIRST=1 ;;
    A | B | C) PARTS+=("$a") ;;
    *) sed -n '2,10p' "$0"; exit 2 ;;
  esac
done
[ ${#PARTS[@]} -gt 0 ] || PARTS=(A B C)

API=http://localhost:${API_PORT:-8000}
WEB=http://localhost:${WEB_PORT:-3000}
LIVE_API=https://dephgraph-loki-api.vercel.app
LIVE_WEB=https://dephgraph-loki.vercel.app
NET=dephgraph_default
PG=postgresql+asyncpg://dephgraph:dephgraph@postgres:5432/dephgraph
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

b=$'\e[1m' d=$'\e[2m' c=$'\e[36m' r=$'\e[0m'
part() { printf '\n%s══ %s ══%s\n' "$b" "$1" "$r"; }
# say "<id> <title>" "<what to say>": shows the talking point, then waits for Enter.
say() {
  printf '\n%s%s%s\n%sSay:%s %s\n' "$b" "$1" "$r" "$c" "$r" "$2"
  [ "$PAUSE" = 0 ] || read -rp "${d}[Enter to run]${r} " _
}
# run <command...>: echoes the command, then runs it; a failed step is reported, not fatal.
run() {
  printf '%s$ %s%s\n' "$d" "$*" "$r"
  "$@" || printf '%s(step exited with %s)%s\n' "$c" "$?" "$r"
}
show() { printf '%sShow:%s %s\n' "$c" "$r" "$*"; }
api_py() {  # a repo script inside the API image, on the compose network
  docker run --rm --network "$NET" -v "$PWD:/srv/dephgraph:z" -w /srv/dephgraph \
    -e DATABASE_URL="$PG" -e MONGO_URI=mongodb://mongo:27017 -e REDIS_URL=redis://redis:6379/0 \
    -e GROQ_API_KEY "$@"
}
pretty() { python3 -m json.tool 2>/dev/null || cat; }

# ---- 0. Setup ------------------------------------------------------------------------------------
part "0. Setup"
run docker compose up -d --wait
run scripts/mysql.sh up
run scripts/mysql.sh run er-diagram/schema.sql
run scripts/mysql.sh run sql/seed.sql
if [ "$FIRST" = 1 ]; then
  run api_py dephgraph-api python scripts/seed_demo_extra.py
  run docker compose --profile ml run --rm cluster
fi
T=$(docker exec dephgraph-web-1 cat /shared/dev_token)
H="Authorization: Bearer $T"
export GROQ_API_KEY=${GROQ_API_KEY:-$(sed -n 's/^GROQ_API_KEY=//p' .env.local 2>/dev/null | tr -d '"')}
[ -n "$GROQ_API_KEY" ] || echo "No GROQ_API_KEY: the PR gate stops at stage 2 (by design)."
echo "Dashboard: $WEB   ER figures: er-diagram/images/"

# ---- A. Database design and SQL (MySQL) ----------------------------------------------------------
partA() {
  part "A. Database design and SQL (MySQL), about 7 minutes"
  say "A1 ER diagram" "14 entities, 5 weak; recursive DEPENDS_ON; ternary OBSERVES."
  show er-diagram/images/er_chen.png
  say "A2 Relational model" "The 7-step mapping: 9 + 5 + 0 + 0 + 6 + 2 + 1 = 23 relations, 26 FKs."
  show "relational-model/README.md (the 7-step table)"
  say "A3 Normalisation" "Flat SCAN_REPORT to BCNF via the minimal cover; both methods end at the same tables."
  show "normalization/README.md"
  say "A4 Constraints" "Each constraint rejects bad data; then cascade and set null. Expect 'DONE with 10 error(s)': 1452, 1048, 1062, 3819, 1451; D2 removes 11 rows."
  run scripts/mysql.sh run --force sql/constraint_demos.sql
  say "A5 Queries" "Recursive CTE = transitive closure (Q1b); division with double NOT EXISTS (Q6). Each query prints its expected result first."
  run scripts/mysql.sh run sql/queries.sql
  show "logs/queries.log, Q1b and Q6 (Q1: payments-svc reaches log4j-core 2.14.1 at level 1)"
  say "A6 Deadlock" "Opposite lock order makes a wait-for cycle; InnoDB rolls one back. Expect ERROR 1213 and 'data restored: yes'."
  run bash scripts/tx_two_sessions.sh deadlock
  run cat logs/tx_deadlock.log
  # Leave the course DB as it was before the demo.
  scripts/mysql.sh run er-diagram/schema.sql >/dev/null && scripts/mysql.sh run sql/seed.sql >/dev/null
}

# ---- B. The application (local stack) ------------------------------------------------------------
partB() {
  part "B. The application, about 8 minutes"
  say "B1 Dashboard ($WEB)" "Same 23-table design on PostgreSQL. Overview counts come from SQL views; checkout-web graph with blast radius (recursive closure + BFS, cached in Redis 24 h); Vulnerabilities (OSV, CVSS); Remediation for team Platform (greedy set cover); Clusters (SBERT, UMAP, HDBSCAN, c-TF-IDF)."
  show "$WEB in the browser: Overview, Services > checkout-web, Vulnerabilities, Remediation > Platform, Clusters"

  say "B2.1 Ingest a manifest" "Raw file goes to MongoDB first, advisories are stored raw, then one PostgreSQL transaction with an advisory lock writes packages, vulnerabilities, the deployment and its closure. Needs internet (live OSV.dev)."
  python3 -c "import json,sys;print(json.dumps({'service_id':1,'ecosystem':'pypi','filename':'requirements.txt','content':open(sys.argv[1]).read()}))" \
    apps/api/tests/fixtures/requirements_flask.txt >"$TMP/ing.json"
  printf '%s$ curl -X POST %s/ingest/manifest (requirements_flask.txt)%s\n' "$d" "$API" "$r"
  curl -s -X POST -H "$H" -H 'Content-Type: application/json' -d @"$TMP/ing.json" "$API/ingest/manifest" |
    tee "$TMP/ing.out" | pretty
  # The same file is stored once (by hash), so look the document up by the id the ingest returned.
  MID=$(python3 -c "import json,sys;print(json.load(sys.stdin)['data']['manifest_id'])" <"$TMP/ing.out" 2>/dev/null || true)

  say "B2.2 Both stores" "The Mongo document is now 'projected' with the same deploy_seq that PostgreSQL shows."
  run docker exec dephgraph-mongo-1 mongosh dephgraph --quiet --eval \
    "db.raw_manifests.find({_id: ObjectId('$MID')},{raw_content:0,parsed_packages:0,deploy_seqs:0})"
  run docker exec dephgraph-postgres-1 psql -U dephgraph dephgraph -c \
    "SELECT service_id, deploy_seq, deployed_at FROM deployment WHERE service_id = 1 ORDER BY deploy_seq DESC LIMIT 2;"

  say "B2.3 Runtime liveness" "Only flask and werkzeug ran; the other packages are now prunable (refresh api-gateway: Prunable list). Expect observes_written 8, live_packages 2."
  printf '%s$ curl -X POST %s/runtime/samples (flask, werkzeug)%s\n' "$d" "$API" "$r"
  curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/runtime/samples" \
    -d '{"service_id":1,"complete":true,"samples":[{"name":"flask","version":"2.0.1","exec_count":120},{"name":"werkzeug","version":"2.0.1","exec_count":80}]}' | pretty

  say "B2.4 PR gate" "Stage 1 = fast exit, stage 2 = SQL rule table (CVSS, copyleft, abandonment, redundancy), stage 3 = LLM may only escalate, never relax. Verdicts follow live OSV and registry data, so they can change between runs."
  run api_py dephgraph-api python scripts/pr_gate_dry_run.py --service 1 \
    --dep pypi:httpx:0.27.0 --dep pypi:werkzeug:2.0.1 --dep npm:lodash:4.17.15
}

# ---- C. The live deployment ----------------------------------------------------------------------
partC() {
  part "C. The live deployment, about 1 minute"
  say "C1 Live API" "Two Vercel projects from one monorepo, with Supabase, MongoDB Atlas and Upstash Redis. Expect 200 from /health, then 401 from /me (no token). A first request after idle can be a cold start."
  run curl -s -w '  %{http_code} %{time_total}s\n' "$LIVE_API/health"
  run curl -s -o /dev/null -w '/me without a token: %{http_code}\n' "$LIVE_API/me"
  say "C2 Live site" "Invite-only access with GitHub sign-in; schedules and clustering run in GitHub Actions. No seed data in production."
  show "$LIVE_WEB in the browser (login page only, not your account pages)"
}

for p in "${PARTS[@]}"; do "part$p"; done
printf '\n%sDemo finished.%s\n' "$b" "$r"
