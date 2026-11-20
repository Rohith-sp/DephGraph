#!/usr/bin/env bash
# DephGraph — Phase 5: run concurrent MySQL sessions side by side (transactions/README.md).
#
#   scripts/tx_two_sessions.sh                 # run every demo below
#   scripts/tx_two_sessions.sh deadlock        # run one (or several) demos by name
#   scripts/tx_two_sessions.sh ingest          # single-session atomicity demo, sql/tx_ingest_demo.sql
#   DB=dephgraph_tx scripts/tx_two_sessions.sh # use another database (default: dephgraph)
#
# Demos (SQL in sql/tx/<demo>_<session>.sql, one file per session):
#   lockwait       A holds FOR UPDATE on an AFFECTS row; B waits (ERROR 1205 with a 1 s timeout,
#                  then a real wait); M reads performance_schema.data_locks / data_lock_waits
#   deadlock       A and B lock AFFECTS and VULNERABILITY in opposite order -> ERROR 1213 for B
#   dirtyread      A reads B's uncommitted change under READ UNCOMMITTED (not under RC / RR)
#   nonrepeatable  A (READ COMMITTED) and C (REPEATABLE READ) read the same rows twice
#                  while B commits a change in between
#   lostupdate     two read-modify-write transactions: plain SELECT loses an update, FOR UPDATE does not
#
# How the timing works: the script picks a start time T0 (1.5 s in the future) and starts every
# session at once (one `docker exec ... mysql` client each, in the background). Each session gets
# @t0 through --init-command and waits with DO SLEEP until "t = x s" before each step, so the order
# of steps is the same on every run. Output lines are prefixed with the time (seconds after T0)
# at which the client printed them (= when the statement finished) and the session name, then
# merged in time order into logs/tx_<demo>.log.
#
# Every demo ends with ROLLBACK or a compensating UPDATE; the script checks with CHECKSUM TABLE
# that the data is the same before and after. Needs schema.sql + seed.sql loaded into $DB.
# Requires the course MySQL container (scripts/mysql.sh up). No password is used here: the
# container's /root/.my.cnf logs the client in.
set -euo pipefail
export LC_ALL=C

CONTAINER=${CONTAINER:-dephgraph-mysql}
DB=${DB:-dephgraph}
ALL_DEMOS=(lockwait deadlock dirtyread nonrepeatable lostupdate)
CHECKED_TABLES="affects, vulnerability, remediation_action, deployment, resolves_to"

cd "$(dirname "$0")/.."
mkdir -p logs

mysql_q() { docker exec -i "$CONTAINER" mysql "$@"; }

# ---------------------------------------------------------------------------------------------
# Prefix every output line of one session with "<seconds since T0>  <session> |".
# Drops the client's "--------------" separators, blank lines and the echo of SQL comments, and
# prints the timing statements "DO SLEEP(GREATEST(0, @t0 + x - ...))" as "(waits until t = x s)".
# (Each comment and each timing statement is followed by "Query OK, 0 rows affected": skipped too.)
label() {
  local tag=$1 line skip=0 next us x
  while IFS= read -r line; do
    us=$(( ${EPOCHREALTIME/./} - T0_US ))
    next=0
    case $line in
      --------------|''|Bye) continue ;;
      '--'|'-- '*) skip=1; continue ;;
      'DO SLEEP(GREATEST(0, @t0 + '*)
        x=${line#*@t0 + }; x=${x%% -*}
        line="(waits until t = $x s)"; next=1 ;;
      'Query OK, 0 rows affected')
        if (( skip )); then skip=0; continue; fi ;;
    esac
    skip=$next
    printf '%7.2f  %s | %s\n' "${us}e-6" "$tag" "$line"
  done
}

run_session() {   # $1 = session name, $2 = SQL file
  # stderr is merged into stdout inside the container, so an error line always lands in the same
  # place relative to the statement text (two separate docker streams would race).
  docker exec -i "$CONTAINER" sh -c 'exec mysql "$@" 2>&1' mysql --table -vv --unbuffered --force \
    --init-command="SET @t0 = $T0, @session_label = '$1', SESSION innodb_lock_wait_timeout = 5" \
    "$DB" < "$2" | label "$1"
}

checksum() { mysql_q -N "$DB" -e "CHECKSUM TABLE $CHECKED_TABLES" | awk '{print $NF}' | paste -sd' '; }

deadlock_section() {
  mysql_q -N -r -e "SHOW ENGINE INNODB STATUS" \
    | sed -n '/^LATEST DETECTED DEADLOCK$/,/^TRANSACTIONS$/p' | sed '$d'
}

check_db() {
  if ! mysql_q -N -e "SELECT COUNT(*) FROM \`$DB\`.affects WHERE vuln_id = 900" 2>/dev/null | grep -qx 1; then
    echo "Database '$DB' is missing or has no seed data. Load er-diagram/schema.sql and sql/seed.sql first." >&2
    exit 1
  fi
}

run_demo() {
  local demo=$1 log="logs/tx_$1.log" tmp before after f tag
  local files=(sql/tx/"$demo"_*.sql)
  [ -e "${files[0]}" ] || { echo "unknown demo: $demo (have: ${ALL_DEMOS[*]} ingest)" >&2; exit 1; }
  tmp=$(mktemp -d)
  before=$(checksum)

  T0_US=$(( ${EPOCHREALTIME/./} + 1500000 ))
  T0=$(printf '%d.%06d' $(( T0_US / 1000000 )) $(( T0_US % 1000000 )))
  for f in "${files[@]}"; do
    tag=${f##*_}; tag=${tag%.sql}; tag=${tag^^}
    run_session "$tag" "$f" > "$tmp/$tag" &
  done
  wait
  after=$(checksum)

  {
    echo "== DephGraph Phase 5 demo: $demo"
    echo "== database $DB, MySQL $(mysql_q -N -e 'SELECT @@version'), run $(date '+%Y-%m-%d %H:%M:%S')"
    echo "== sessions: $(cd "$tmp" && ls | paste -sd' ')   (time = seconds after T0, when the statement finished)"
    echo
    cat "$tmp"/* | sort -s -n -k1,1
    if [ "$demo" = deadlock ]; then
      echo
      echo "== SHOW ENGINE INNODB STATUS, section LATEST DETECTED DEADLOCK"
      deadlock_section
    fi
    echo
    if [ "$before" = "$after" ]; then
      echo "== data restored: yes (CHECKSUM TABLE $CHECKED_TABLES unchanged)"
    else
      echo "== data restored: NO, checksums before [$before] after [$after]"
    fi
  } > "$log"
  rm -rf "$tmp"
  echo "$demo -> $log: $(grep -c '| ERROR' "$log" || true) error line(s), $(tail -1 "$log" | cut -d'(' -f1 | cut -c4-)"
}

run_ingest() {
  local log=logs/tx_ingest_demo.log
  # Same file as `scripts/mysql.sh run --force sql/tx_ingest_demo.sql`, pointed at $DB, and with the
  # client's echo of comment lines (each followed by "Query OK, 0 rows affected") removed.
  sed "s/^USE dephgraph;/USE \`$DB\`;/" sql/tx_ingest_demo.sql \
    | docker exec -i "$CONTAINER" sh -c 'exec mysql --table --show-warnings --force --unbuffered -vv 2>&1' \
    | awk '
        /^--------------$/ || /^Bye$/ { next }
        /^--( |$)/ { skip = 1; next }
        skip && (/^$/ || /^Query OK, 0 rows affected$/) { next }
        { skip = 0; print }' \
    | cat -s > "$log"
  echo "ingest -> $log ($(grep -c '^ERROR' "$log") expected error(s))"
}

check_db
if [ $# -eq 0 ]; then set -- "${ALL_DEMOS[@]}"; fi
for demo in "$@"; do
  if [ "$demo" = ingest ]; then run_ingest; else run_demo "$demo"; fi
done
