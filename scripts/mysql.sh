#!/usr/bin/env bash
# Course MySQL 8.4 (separate from the app's docker compose stack), run in Docker.
# Linux/macOS replacement for scripts/run-sql.ps1.
#
#   scripts/mysql.sh up                           # create/start the container (first run makes a random root password)
#   scripts/mysql.sh run er-diagram/schema.sql    # run a .sql file, output also saved to logs/schema.log
#   scripts/mysql.sh run file.sql logs/other.log  # same, with a chosen log file
#   scripts/mysql.sh run --force sql/constraint_demos.sql  # keep going after errors, echo each statement
#   scripts/mysql.sh shell                        # interactive mysql prompt
#   scripts/mysql.sh password                     # print the root password (for MySQL Workbench)
#   scripts/mysql.sh down                         # stop the container (data is kept in a Docker volume)
#
# The root password lives only in ~/.config/dephgraph/mysql.cnf (mode 600), never in the repo.
# MySQL listens on 127.0.0.1:${MYSQL_PORT:-3306} for Workbench (user root).
set -euo pipefail

CONTAINER=dephgraph-mysql
IMAGE=mysql:8.4
VOLUME=dephgraph_mysql_data
PORT=${MYSQL_PORT:-3306}
CNF_DIR="$HOME/.config/dephgraph"
CNF="$CNF_DIR/mysql.cnf"

cd "$(dirname "$0")/.."

password() { sed -n 's/^password=//p' "$CNF"; }

ensure_cnf() {
  [ -f "$CNF" ] && return
  mkdir -p "$CNF_DIR" && chmod 700 "$CNF_DIR"
  (umask 077; printf '[client]\nuser=root\npassword=%s\ndefault-character-set=utf8mb4\n' \
    "$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')" > "$CNF")
  echo "Created $CNF (root password, mode 600)"
}

wait_ready() {
  printf 'Waiting for MySQL'
  for _ in $(seq 1 90); do
    # The entrypoint runs a temporary server without TCP first; wait for the real one on TCP.
    if docker exec "$CONTAINER" mysqladmin --protocol=tcp -h127.0.0.1 ping >/dev/null 2>&1; then
      echo ' ready'; return
    fi
    printf '.'; sleep 2
  done
  echo; echo "MySQL did not start; see: docker logs $CONTAINER" >&2; exit 1
}

# Copy the client config into the container so `mysql` inside it logs in without a password argument.
install_cnf() {
  docker exec -i "$CONTAINER" sh -c 'cat > /root/.my.cnf && chmod 600 /root/.my.cnf' < "$CNF"
}

up() {
  ensure_cnf
  if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    docker start "$CONTAINER" >/dev/null
  else
    docker run -d --name "$CONTAINER" \
      -p "127.0.0.1:$PORT:3306" \
      -v "$VOLUME:/var/lib/mysql" \
      -e MYSQL_ROOT_PASSWORD="$(password)" \
      "$IMAGE" >/dev/null
  fi
  install_cnf
  wait_ready
  echo "MySQL 8.4 up on 127.0.0.1:$PORT (container $CONTAINER)"
}

run() {
  # --force: for demos that expect errors. Keep going after an error and echo every statement
  # with its result, so the log shows each statement next to the error or rows it produced.
  local force=()
  if [ "${1:-}" = --force ]; then force=(--force --unbuffered -vv); shift; fi
  local file=${1:?usage: scripts/mysql.sh run [--force] FILE.sql [LOG]}
  local log=${2:-logs/$(basename "${file%.*}").log}
  mkdir -p "$(dirname "$log")"
  set +e
  docker exec -i "$CONTAINER" mysql --table --show-warnings ${force[@]+"${force[@]}"} < "$file" 2>&1 | tee "$log"
  local code=${PIPESTATUS[0]}
  set -e
  echo "exit code: $code" >> "$log"
  if [ ${#force[@]} -gt 0 ]; then
    echo "DONE with $(grep -c '^ERROR' "$log") error(s), see which were expected -> $log"; return 0
  fi
  if [ "$code" -eq 0 ]; then echo "OK -> $log"; else echo "FAILED (exit $code) -> $log" >&2; fi
  return "$code"
}

case "${1:-}" in
  up)       up ;;
  run)      shift; run "$@" ;;
  shell)    docker exec -it "$CONTAINER" mysql ;;
  password) password ;;
  down)     docker stop "$CONTAINER" >/dev/null && echo stopped ;;
  *)        sed -n '2,13p' "$0"; exit 1 ;;
esac
