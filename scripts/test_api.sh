#!/usr/bin/env bash
# Run the backend tests in the API image against the compose Postgres/Mongo/Redis, using an
# isolated test database so several runs can go in parallel (one per worktree).
#   scripts/test_api.sh                      # db dephgraph_test, redis db 15
#   TEST_NAME=a1 TEST_REDIS_DB=11 scripts/test_api.sh -k runtime
# Needs `docker compose up -d` (network dephgraph_default) and the dephgraph-api image.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
docker run --rm --network dephgraph_default \
  -v "$ROOT:/srv/dephgraph:z" -v dephgraph_hf_cache:/hf-cache -e HF_HOME=/hf-cache \
  -w /srv/dephgraph/apps/api \
  -e DATABASE_URL=postgresql+asyncpg://dephgraph:dephgraph@postgres:5432/dephgraph \
  -e MONGO_URI=mongodb://mongo:27017 -e REDIS_URL=redis://redis:6379/0 \
  -e DEPHGRAPH_TEST_DB="dephgraph_test${TEST_NAME:+_$TEST_NAME}" \
  -e DEPHGRAPH_TEST_REDIS_DB="${TEST_REDIS_DB:-15}" \
  dephgraph-api python -m pytest tests -q -p no:cacheprovider --cov=app --cov-fail-under=70 "$@"
