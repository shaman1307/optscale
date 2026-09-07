#!/usr/bin/env bash
set -euo pipefail

# Show report-import queue health and the corresponding OptScale import records.
# Run from this directory or any other directory:
#   ./import-queue-status.sh
#   ./import-queue-status.sh --details
#   ./import-queue-status.sh --details 50
#   ./import-queue-status.sh --clean-logs

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
SHOW_DETAILS=false
CLEAN_LOGS=false
LIMIT=25

while [[ $# -gt 0 ]]; do
    case "$1" in
        --details)
            SHOW_DETAILS=true
            if [[ "${2:-}" =~ ^[1-9][0-9]*$ ]]; then
                LIMIT="$2"
                shift 2
            else
                shift
            fi
            ;;
        --clean-logs)
            CLEAN_LOGS=true
            shift
            ;;
        -h|--help)
            cat <<EOF
Usage: $(basename "$0") [--details [N]] [--clean-logs]

  --details [N]   List up to N active/queued imports (default 25)
  --clean-logs    Truncate Docker JSON logs (skips diworker; keeps running-job markers)

  REMAP_POOL_ID   Optional env: force remapping progress tables for a
                  project pool UUID even when no apply job is running
EOF
            exit 0
            ;;
        *)
            echo "Usage: $(basename "$0") [--details [N]] [--clean-logs]" >&2
            exit 1
            ;;
    esac
done

if ! [[ "$LIMIT" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: details limit must be a positive number." >&2
    exit 1
fi

# Truncate Docker json-file logs. Needs root for /var/lib/docker/containers
# (docker group grants the API socket, not filesystem access).
#
# Skip diworker: its JSON log holds "Started import" / phase lines for the
# current run (active_h + status). Truncating it mid-flight blanks those.
clean_docker_json_logs() {
    printf '=== Cleaning Docker container logs ===\n'
    local cleaned=0
    local skipped=0
    local bytes_before=0
    local use_sudo=false
    local log
    local skip_ids=""

    # Full container ids whose JSON logs must stay (running import markers).
    skip_ids="$(
        cd "$SCRIPT_DIR"
        docker compose ps -q diworker 2>/dev/null | tr '\n' ' '
    )"
    skip_ids="${skip_ids%"${skip_ids##*[![:space:]]}"}"

    should_skip_log() {
        local path="$1"
        local cid
        [[ -z "$skip_ids" ]] && return 1
        # .../containers/<container_id>/<container_id>-json.log
        cid="$(basename "$(dirname "$path")")"
        case " $skip_ids " in
            *" $cid "*) return 0 ;;
            *) return 1 ;;
        esac
    }

    # Glob must run as root: ilya's shell cannot expand
    # /var/lib/docker/containers/* before sudo.
    if ls /var/lib/docker/containers/*/*-json.log >/dev/null 2>&1; then
        use_sudo=false
    elif command -v sudo >/dev/null 2>&1 \
            && sudo find /var/lib/docker/containers -name '*-json.log' \
                -type f -print -quit 2>/dev/null | grep -q .; then
        use_sudo=true
    else
        echo "ERROR: cannot read /var/lib/docker/containers (need sudo)." >&2
        echo "Hint: sudo ./import-queue-status.sh --clean-logs" >&2
        return 1
    fi

    if [[ "$use_sudo" == true ]]; then
        local list
        list="$(sudo find /var/lib/docker/containers -name '*-json.log' -type f 2>/dev/null || true)"
        while IFS= read -r log; do
            [[ -n "$log" ]] || continue
            if should_skip_log "$log"; then
                skipped=$((skipped + 1))
                continue
            fi
            local sz
            sz="$(sudo stat -c%s "$log" 2>/dev/null || echo 0)"
            bytes_before=$((bytes_before + sz))
            if [[ "$sz" -gt 0 ]]; then
                sudo truncate -s 0 "$log"
                cleaned=$((cleaned + 1))
            fi
        done <<<"$list"
    else
        for log in /var/lib/docker/containers/*/*-json.log; do
            [[ -f "$log" ]] || continue
            if should_skip_log "$log"; then
                skipped=$((skipped + 1))
                continue
            fi
            local sz
            sz="$(stat -c%s "$log" 2>/dev/null || echo 0)"
            bytes_before=$((bytes_before + sz))
            if [[ "$sz" -gt 0 ]]; then
                : >"$log" || truncate -s 0 "$log"
                cleaned=$((cleaned + 1))
            fi
        done
    fi

    if [[ "$skipped" -gt 0 ]]; then
        printf 'Skipped %s diworker log file(s) (keep Started import / phase for running jobs).\n' \
            "$skipped"
    fi
    printf 'Truncated %s log file(s); freed ~%s before truncate.\n' \
        "$cleaned" "$(numfmt --to=iec --suffix=B "$bytes_before" 2>/dev/null || echo "${bytes_before}B")"
    printf 'Disk after cleanup:\n'
    df -h / | awk 'NR==1 || NR==2'
}

if [[ "$CLEAN_LOGS" == true ]]; then
    # Skip the heavy queue scan when only cleanup was requested.
    if [[ "$SHOW_DETAILS" == false ]]; then
        clean_docker_json_logs
        exit 0
    fi
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: ${ENV_FILE} does not exist." >&2
    echo "Run this script on the OptScale Docker host (vmoptscale), not on the local Mac." >&2
    exit 1
fi

mariadb_password="$(awk -F= '$1 == "MARIADB_ROOT_PASSWORD" { print substr($0, length($1) + 2); exit }' "$ENV_FILE")"
if [[ -z "$mariadb_password" ]]; then
    echo "ERROR: MARIADB_ROOT_PASSWORD is not configured in ${ENV_FILE}." >&2
    exit 1
fi
mongo_user="$(awk -F= '$1 == "MONGO_ROOT_USER" { print substr($0, length($1) + 2); exit }' "$ENV_FILE")"
mongo_password="$(awk -F= '$1 == "MONGO_ROOT_PASSWORD" { print substr($0, length($1) + 2); exit }' "$ENV_FILE")"

cd "$SCRIPT_DIR"

# Display timestamps in the operator timezone. VM MariaDB/host are usually UTC;
# override with IMPORT_STATUS_TZ or TZ (e.g. Europe/Berlin).
HOST_TZ="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
if [[ -n "${IMPORT_STATUS_TZ:-}" ]]; then
    DISPLAY_TZ="$IMPORT_STATUS_TZ"
elif [[ -n "${TZ:-}" ]]; then
    DISPLAY_TZ="$TZ"
elif [[ -n "$HOST_TZ" && "$HOST_TZ" != "UTC" && "$HOST_TZ" != "Etc/UTC" ]]; then
    DISPLAY_TZ="$HOST_TZ"
else
    DISPLAY_TZ="Europe/Berlin"
fi
export TZ="$DISPLAY_TZ"
MYSQL_TZ_OFFSET="$(date +%:z)"

mysql_query() {
    docker compose exec -T -e "MYSQL_PWD=${mariadb_password}" mariadb \
        mysql -uroot --table my-db -e "$1"
}

mysql_value() {
    docker compose exec -T -e "MYSQL_PWD=${mariadb_password}" mariadb \
        mysql -uroot --batch --raw --skip-column-names my-db -e "$1"
}

organization_row="$(docker compose exec -T -e "MYSQL_PWD=${mariadb_password}" mariadb \
    mysql -uroot --batch --raw --skip-column-names my-db -e "
        SELECT CONCAT(o.id, '|', o.name)
        FROM organization o
        JOIN cloudaccount ca ON ca.organization_id = o.id
        WHERE o.deleted_at = 0
          AND o.disabled = 0
          AND ca.deleted_at = 0
        GROUP BY o.id, o.name
        ORDER BY COUNT(ca.id) DESC, o.created_at DESC
        LIMIT 1;")"

if [[ -z "$organization_row" ]]; then
    echo "ERROR: no active organization with cloud accounts was found." >&2
    exit 1
fi

ORGANIZATION_ID="${organization_row%%|*}"
ORGANIZATION_NAME="${organization_row#*|}"
organization_filter="AND ca.organization_id = '${ORGANIZATION_ID}'"
organization_subquery_filter="AND organization_id = '${ORGANIZATION_ID}'"

printf '=== Organization scope: %s ===\n' "$ORGANIZATION_NAME"

printf '\n=== RabbitMQ pool for diworker ===\n'
rabbit_queues_tsv="$(docker compose exec -T rabbitmq rabbitmqctl list_queues \
    name messages_ready messages_unacknowledged consumers 2>/dev/null \
    | awk 'NR > 1 && $1 ~ /^report-imports/ { print $1 "\t" $2 "\t" $3 "\t" $4 }')"
printf '%s\n' "$rabbit_queues_tsv" | python3 -c '
import sys
rows = []
for line in sys.stdin.read().splitlines():
    if not line.strip():
        continue
    parts = line.split("\t")
    if len(parts) < 4:
        continue
    rows.append({
        "queue": parts[0],
        "ready": parts[1],
        "unacked": parts[2],
        "consumers": parts[3],
    })
headers = ("queue", "ready", "unacked", "consumers")
widths = {h: len(h) for h in headers}
for row in rows:
    for h in headers:
        widths[h] = max(widths[h], len(row[h]))

def fmt(row):
    return "| " + " | ".join(row[h].ljust(widths[h]) for h in headers) + " |"

sep = "+-" + "-+-".join("-" * widths[h] for h in headers) + "-+"
print(sep)
print(fmt({h: h for h in headers}))
print(sep)
for row in rows:
    print(fmt(row))
print(sep)
'

# Open failures = FAILED with no later import for the same account
# (last run for that connector ended in failure).
open_failed_predicate="
    ri.state = 'FAILED'
    AND NOT EXISTS (
        SELECT 1
        FROM reportimport later_ri
        WHERE later_ri.cloud_account_id = ri.cloud_account_id
          AND later_ri.deleted_at = 0
          AND later_ri.created_at > ri.created_at
    )"

printf '\n=== Tasks and status from MariaDB (by vendor) ===\n'
# failed = open failures only (last run per account).
# Cluster-wide so system accounts (e.g. environment) appear with other vendors.
progress_tsv="$(docker compose exec -T -e "MYSQL_PWD=${mariadb_password}" mariadb \
    mysql -uroot --batch --raw --skip-column-names my-db -e "
    SELECT
        LOWER(COALESCE(parent.type, ca.type)),
        COALESCE(parent.id, ca.id),
        COALESCE(parent.name, ca.name),
        SUM(ri.state = 'SCHEDULED'),
        SUM(ri.state = 'IN_PROGRESS'),
        -- Live workers: diworker heartbeats active imports every 5 minutes
        -- (HEARTBEAT_INTERVAL). Window 10m covers a missed beat; duration of
        -- the job itself (hours) does not matter.
        SUM(ri.state = 'IN_PROGRESS'
            AND ri.updated_at >= UNIX_TIMESTAMP() - 600),
        SUM(${open_failed_predicate}),
        COUNT(*)
    FROM reportimport ri
    JOIN cloudaccount ca ON ca.id = ri.cloud_account_id
    LEFT JOIN cloudaccount parent ON parent.id = ca.parent_id
    WHERE ri.deleted_at = 0
      AND ca.deleted_at = 0
    GROUP BY LOWER(COALESCE(parent.type, ca.type)),
             COALESCE(parent.id, ca.id), COALESCE(parent.name, ca.name)
    ORDER BY SUM(ri.state = 'IN_PROGRESS') DESC,
             SUM(ri.state = 'SCHEDULED') DESC,
             SUM(${open_failed_predicate}) DESC,
             COALESCE(parent.name, ca.name) ASC;
")"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
printf '%s\n' "$progress_tsv" >"${tmp_dir}/progress.tsv"
printf '%s\n' "$rabbit_queues_tsv" >"${tmp_dir}/rabbit.tsv"
timeout 45 docker compose logs --since 45m --timestamps diworker 2>/dev/null \
    | grep -E 'Started import for |Import phase for |Skipping GCP raw load for |Loading GCP billing for |GCP incremental raw load for |GCP incremental rebuild for |GCP raw load progress for |GCP raw load finished for |Snowflake raw load progress for |Snowflake raw load finished for |Clean progress for ' \
    >"${tmp_dir}/diworker.log" || true
# One line per job. 48h covers long GCP reloads; do not reuse the 45m
# progress grep (that is per-row and must stay short).
timeout 20 docker compose logs --since 48h --timestamps diworker 2>/dev/null \
    | grep 'Started import for ' \
    >"${tmp_dir}/started.log" || true

python3 - "$tmp_dir" <<'PY'
import re
import sys
from pathlib import Path

tmp = Path(sys.argv[1])

progress_rows = []
for line in tmp.joinpath("progress.tsv").read_text().splitlines():
    if not line.strip():
        continue
    parts = line.split("\t")
    if len(parts) < 8:
        continue
    vendor = parts[0]
    if vendor == "environment":
        vendor = "system"
    progress_rows.append({
        "vendor": vendor,
        "id": parts[1],
        "name": parts[2],
        "queued": int(parts[3]),
        "in_progress": int(parts[4]),
        "working": int(parts[5]),
        "failed": int(parts[6]),
        "total": int(parts[7]),
    })

uuid_re = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)

log_path = tmp / "diworker.log"
log_text = log_path.read_text() if log_path.exists() else ""

# Last import pipeline phase / within-phase % per cloud_account_id.
# First GCP load: one BQ stream; "raw load finished" means Mongo is done
# (ClickHouse/Clean). Incremental still rebuilds one usage day at a time —
# only then treat finished as window i/N and keep Mongo until real Clean.
phase_by_ca = {}
progress_by_ca = {}
gcp_windows_total = {}
gcp_windows_done = {}


def gcp_mongo_pct(ca, window_pct):
    n = gcp_windows_total.get(ca)
    if not n:
        return min(100, window_pct)
    done = gcp_windows_done.get(ca, 0)
    return min(100, int((done * 100 + window_pct) / n))


for raw in log_text.splitlines():
    m = re.search(r"Import phase for (" + uuid_re.pattern + r"): (.+)$", raw)
    if m:
        ca, phase = m.group(1), m.group(2).strip()
        phase_by_ca[ca] = phase
        if phase == "Clean" and gcp_windows_total.get(ca, 0) > 1:
            # Multi-day incremental: drop Mongo i/N % so Clean % can take over.
            progress_by_ca.pop(ca, None)
        continue
    m = re.search(r"Skipping GCP raw load for (" + uuid_re.pattern + r")", raw)
    if m:
        phase_by_ca[m.group(1)] = "Clean"
        continue
    # GCP BQ read + deserialize + Mongo insert are one streaming job —
    # surface the whole raw-load stage as MongoDB (not separate BQ/Deser).
    m = re.search(r"Loading GCP billing for (" + uuid_re.pattern + r")", raw)
    if m:
        phase_by_ca[m.group(1)] = "MongoDB"
        continue
    m = re.search(
        r"GCP incremental raw load for (" + uuid_re.pattern +
        r"): .*full-rebuild (\d+) usage window",
        raw,
    )
    if m:
        ca = m.group(1)
        gcp_windows_total[ca] = int(m.group(2))
        gcp_windows_done[ca] = 0
        phase_by_ca[ca] = "MongoDB"
        # Do not pin 0%: real pct comes from "GCP raw load progress ... pct=N"
        # (BQ rows consumed by merge / job.total_rows).
        continue
    m = re.search(
        r"GCP incremental rebuild for (" + uuid_re.pattern +
        r"): usage window",
        raw,
    )
    if m:
        ca = m.group(1)
        phase_by_ca[ca] = "MongoDB"
        continue
    m = re.search(r"GCP raw load progress for (" + uuid_re.pattern + r")", raw)
    if m:
        ca = m.group(1)
        phase_by_ca[ca] = "MongoDB"
        pct_m = re.search(r"\bpct=(\d+)\b", raw)
        if pct_m:
            progress_by_ca[ca] = gcp_mongo_pct(ca, min(100, int(pct_m.group(1))))
        else:
            seen_m = re.search(r"\bbq_seen=(\d+)\b", raw)
            tot_m = re.search(r"\bbq_total=(\d+)\b", raw)
            if seen_m and tot_m and int(tot_m.group(1)) > 0:
                progress_by_ca[ca] = gcp_mongo_pct(
                    ca,
                    min(100, int(100 * int(seen_m.group(1)) / int(tot_m.group(1)))),
                )
        continue
    m = re.search(
        r"Snowflake raw load progress for (" + uuid_re.pattern + r")", raw
    )
    if m:
        # SF fetch + Mongo insert are one status step (same as GCP raw load).
        phase_by_ca[m.group(1)] = "MongoDB"
        pct_m = re.search(r"\bpct=(\d+)\b", raw)
        if pct_m:
            progress_by_ca[m.group(1)] = min(100, int(pct_m.group(1)))
        continue
    m = re.search(r"GCP raw load finished for (" + uuid_re.pattern + r")", raw)
    if m:
        ca = m.group(1)
        n = gcp_windows_total.get(ca)
        if n and n > 1:
            gcp_windows_done[ca] = gcp_windows_done.get(ca, 0) + 1
            phase_by_ca[ca] = "MongoDB"
            progress_by_ca[ca] = min(100, int(gcp_windows_done[ca] * 100 / n))
        else:
            phase_by_ca[ca] = "Clean"
            progress_by_ca.pop(ca, None)
        continue
    m = re.search(
        r"Snowflake raw load finished for (" + uuid_re.pattern + r")", raw
    )
    if m:
        phase_by_ca[m.group(1)] = "Clean"
        progress_by_ca.pop(m.group(1), None)
        continue
    m = re.search(
        r"Clean progress for (" + uuid_re.pattern + r").*: (\d+)%", raw
    )
    if m:
        phase_by_ca[m.group(1)] = "Clean"
        progress_by_ca[m.group(1)] = int(m.group(2))
        continue
    m = re.search(r"Started import for (" + uuid_re.pattern + r")", raw)
    if m and m.group(1) not in phase_by_ca:
        phase_by_ca[m.group(1)] = "started"
(tmp / "import_phase.tsv").write_text(
    "".join(f"{ca}\t{phase}\n" for ca, phase in phase_by_ca.items())
)
(tmp / "import_progress.tsv").write_text(
    "".join(f"{ca}\t{pct}\n" for ca, pct in progress_by_ca.items())
)

# Active (queued/in_progress) first, then settled vendors.
progress_rows.sort(
    key=lambda r: (
        0 if (r["queued"] + r["in_progress"]) > 0 else 1,
        -r["in_progress"],
        -r["queued"],
        -r["failed"],
        r["name"].lower(),
    )
)

headers = (
    "st", "vendor", "connector", "queued", "in_progress", "working",
    "failed", "total", "progress",
)
# Emoji circles are typically 2 terminal columns wide.
ST_WIDTH = 2
widths = {h: (ST_WIDTH if h == "st" else len(h)) for h in headers}
rows = []
for row in progress_rows:
    # Settled share of all import rows (not tied to open-failed count).
    settled = row["total"] - row["queued"] - row["in_progress"]
    progress = (
        "0.0%"
        if row["total"] == 0
        else f"{100.0 * settled / row['total']:.1f}%"
    )
    active = (row["queued"] + row["in_progress"]) > 0
    out = {
        "st": "🟡" if active else "🟢",
        "vendor": row["vendor"],
        "connector": row["name"],
        "queued": str(row["queued"]),
        "in_progress": str(row["in_progress"]),
        "working": str(row["working"]),
        "failed": str(row["failed"]),
        "total": str(row["total"]),
        "progress": progress,
    }
    rows.append(out)
    for h in headers:
        if h == "st":
            continue
        widths[h] = max(widths[h], len(out[h]))

def pad(h, value):
    if h == "st":
        # Keep emoji left-aligned; pad with spaces to 2 display cols.
        return value + (" " * max(0, ST_WIDTH - 1))
    return value.ljust(widths[h])

def fmt(row):
    return "| " + " | ".join(pad(h, row[h]) for h in headers) + " |"

sep = "+-" + "-+-".join("-" * widths[h] for h in headers) + "-+"
print(sep)
print(fmt({h: h for h in headers}))
print(sep)
for row in rows:
    print(fmt(row))
print(sep)
PY

# first  = last_import_at is 0 → full backfill (~3 months)
# short  = already imported before → incremental window
# status = full pipeline; green=done, yellow=current, gray=pending
# age_h  = hours since enqueue; active_h = hours since worker Started import
#          (reportimport.updated_at is a 5m heartbeat, not start)
# Fetch rows first, then print the section header immediately above the table.
queue_tsv="$(docker compose exec -T -e "MYSQL_PWD=${mariadb_password}" mariadb \
    mysql -uroot --batch --raw --skip-column-names my-db -e "
    SELECT
        CASE
            WHEN COALESCE(ca.last_import_at, 0) = 0 THEN 'first'
            ELSE 'short'
        END,
        ri.state,
        LOWER(COALESCE(parent.type, ca.type)),
        ca.name,
        ca.id,
        ROUND((UNIX_TIMESTAMP() - ri.created_at) / 3600, 1),
        ri.created_at
    FROM reportimport ri
    JOIN cloudaccount ca ON ca.id = ri.cloud_account_id
    LEFT JOIN cloudaccount parent ON parent.id = ca.parent_id
    WHERE ri.deleted_at = 0
      AND ca.deleted_at = 0
      AND ri.state IN ('SCHEDULED', 'IN_PROGRESS')
    ORDER BY
        FIELD(ri.state, 'IN_PROGRESS', 'SCHEDULED'),
        CASE WHEN COALESCE(ca.last_import_at, 0) = 0 THEN 0 ELSE 1 END,
        ri.created_at ASC;
")"

printf '%s\n' "$queue_tsv" >"${tmp_dir}/queue.tsv"
printf '\n=== Unfinished imports (queue) ===\n'
python3 - "$tmp_dir" <<'PY'
import re
import sys
import time
from datetime import datetime
from pathlib import Path

KIND_MARK = {
    "first": "\033[90m●\033[0m",  # gray = first load
    "short": "\033[32m●\033[0m",  # green = incremental
}
GREEN = "\033[32m"
YELLOW = "\033[33m"
DIM = "\033[90m"
RESET = "\033[0m"
ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# Vendor-specific import pipelines (short labels in status column).
# Diworker still logs phase "Clean"; display name is ClickHouse.
PIPELINES = {
    "gcp": ("MongoDB", "ClickHouse"),
    "snowflake": ("MongoDB", "ClickHouse"),
    "snowflake_tenant": ("MongoDB", "ClickHouse"),
    "aws": ("MongoDB", "ClickHouse"),
    "default": ("MongoDB", "ClickHouse"),
}
PHASE_LEGENDS = {
    "gcp": "GCP → Deserialize → MongoDB → Resources clean → ClickHouse",
    "snowflake": "Snowflake → MongoDB → Resources clean → ClickHouse",
    "aws": "AWS → MongoDB → Resources clean → ClickHouse",
    "default": "MongoDB → Resources clean → ClickHouse",
}


def vendor_family(vendor):
    v = (vendor or "").lower()
    if v.startswith("gcp"):
        return "gcp"
    if v.startswith("aws"):
        return "aws"
    if "snowflake" in v:
        return "snowflake"
    return "default"


def phase_index_map(pipeline):
    idx = {
        "queued": -1,
        "started": 0,
        "in_progress": 0,
    }
    for i, name in enumerate(pipeline):
        idx[name] = i
    # Shared / legacy aliases map onto this vendor's pipeline.
    aliases = {
        # Raw-load substeps collapse into MongoDB (same as GCP).
        "BQ": "MongoDB",
        "Deser": "MongoDB",
        "BQ read": "MongoDB",
        "Python deserialize": "MongoDB",
        "Mongo write": "MongoDB",
        "SF": "MongoDB",
        "S3": "MongoDB",
        "Parse": "MongoDB",
        # Diworker logs "Clean"; status label is ClickHouse.
        "Clean": "ClickHouse",
        "Resources clean": "ClickHouse",
        "ClickHouse insert": "ClickHouse",
    }
    for alias, canon in aliases.items():
        if canon in idx:
            idx[alias] = idx[canon]
    # Unknown foreign phase labels → start of pipeline (MongoDB).
    for foreign in ("SF", "S3", "Parse", "BQ", "Deser", "Clean"):
        idx.setdefault(foreign, 0)
    return idx


def visible_len(text):
    return len(ANSI_RE.sub("", text))


def render_pipeline(family, phase, pct=None):
    pipeline = PIPELINES[family]
    index = phase_index_map(pipeline)
    idx = index.get(phase, 0)
    parts = []
    for i, name in enumerate(pipeline):
        if idx < 0:
            parts.append(f"{DIM}{name}{RESET}")
        elif i < idx:
            parts.append(f"{GREEN}{name}{RESET}")
        elif i == idx:
            label = name if pct is None else f"{name} ({pct}%)"
            parts.append(f"{YELLOW}{label}{RESET}")
        else:
            parts.append(f"{DIM}{name}{RESET}")
    return f" {DIM}→{RESET} ".join(parts)


def current_pct(family, phase, ca_id, progress_by_ca):
    pipeline = PIPELINES[family]
    index = phase_index_map(pipeline)
    idx = index.get(phase, 0)
    if idx < 0:
        return None
    # Real % only: Clean/ClickHouse logs "N%", GCP/Snowflake MongoDB logs "pct=N".
    if ca_id in progress_by_ca:
        return progress_by_ca[ca_id]
    return None


tmp = Path(sys.argv[1])
uuid_re = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
COMPOSE_TS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)(Z|[+-]\d{2}:\d{2})?"
)
STARTED_RE = re.compile(r"Started import for (" + uuid_re.pattern + r")")


def parse_compose_ts(line):
    """Unix seconds from `docker compose logs --timestamps` prefix."""
    m = COMPOSE_TS_RE.search(line)
    if not m:
        return None
    stamp = m.group(1)
    tz = m.group(2) or "Z"
    if "." in stamp:
        head, frac = stamp.split(".", 1)
        stamp = head + "." + frac[:6]
    if tz == "Z":
        tz = "+00:00"
    try:
        return datetime.fromisoformat(stamp + tz).timestamp()
    except ValueError:
        return None


def load_started_times(*paths):
    """ca_id -> last 'Started import' unix ts (later job wins)."""
    started = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(errors="replace").splitlines():
            sm = STARTED_RE.search(line)
            if not sm:
                continue
            ts = parse_compose_ts(line)
            if ts is None:
                continue
            started[sm.group(1)] = ts
    return started


phase_by_ca = {}
phase_path = tmp / "import_phase.tsv"
if phase_path.exists():
    for line in phase_path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            phase_by_ca[parts[0]] = parts[1]

progress_by_ca = {}
progress_path = tmp / "import_progress.tsv"
if progress_path.exists():
    for line in progress_path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[1].isdigit():
            progress_by_ca[parts[0]] = int(parts[1])

# 48h start markers first; 45m progress log fills in if the long fetch timed out.
started_by_ca = load_started_times(tmp / "started.log", tmp / "diworker.log")
now_ts = time.time()


def active_hours(state, ca_id, created_ts):
    """Hours the worker has been running; '-' while still queued."""
    if state != "IN_PROGRESS":
        return "-"
    start_ts = started_by_ca.get(ca_id)
    # Ignore a previous job's start still in the 48h log.
    if start_ts is None or start_ts < created_ts - 60:
        return "-"
    return "%.1f" % max(0.0, (now_ts - start_ts) / 3600.0)


rows = []
families_seen = set()
for line in (tmp / "queue.tsv").read_text().splitlines():
    if not line.strip():
        continue
    parts = line.split("\t")
    if len(parts) < 7:
        continue
    vendor = parts[2]
    if vendor == "environment":
        vendor = "system"
    ca_id = parts[4]
    state = parts[1]
    family = vendor_family(vendor)
    families_seen.add(family)
    if state == "SCHEDULED":
        phase = "queued"
    else:
        phase = phase_by_ca.get(ca_id) or "in_progress"
    try:
        created_ts = int(float(parts[6]))
        created = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        created_ts = 0
        created = parts[6]
    rows.append({
        "kind": parts[0],
        "state": state,
        "vendor": vendor,
        "project": parts[3],
        "status": render_pipeline(
            family, phase, current_pct(family, phase, ca_id, progress_by_ca)
        ),
        "age_h": parts[5],
        "active_h": active_hours(state, ca_id, created_ts),
        "created": created,
        "family": family,
    })

if not rows:
    print("No unfinished imports.")
    raise SystemExit(0)

print(
    f"status: {GREEN}done{RESET} | {YELLOW}current{RESET} | {DIM}pending{RESET}"
)
print("  age_h = since enqueue | active_h = since worker started")
for family in ("gcp", "snowflake", "aws", "default"):
    if family not in families_seen:
        continue
    print(f"  {family}: {PHASE_LEGENDS[family]}")

headers = (
    "kind", "state", "vendor", "project", "status", "age_h", "active_h",
    "created",
)
widths = {h: len(h) for h in headers}
for row in rows:
    for h in headers:
        if h == "kind":
            continue
        widths[h] = max(widths[h], visible_len(row[h]))


def cell(h, row):
    if h == "kind":
        mark = KIND_MARK.get(row["kind"], row["kind"])
        return mark + (" " * max(0, widths[h] - 1))
    value = row[h]
    pad = max(0, widths[h] - visible_len(value))
    return value + (" " * pad)


def fmt(row):
    return "| " + " | ".join(cell(h, row) for h in headers) + " |"


def fmt_header():
    return "| " + " | ".join(h.ljust(widths[h]) for h in headers) + " |"


sep = "+-" + "-+-".join("-" * widths[h] for h in headers) + "-+"
print(sep)
print(fmt_header())
print(sep)
for row in rows:
    print(fmt(row))
print(sep)
first = sum(1 for r in rows if r["kind"] == "first")
short = sum(1 for r in rows if r["kind"] == "short")
in_prog = sum(1 for r in rows if r["state"] == "IN_PROGRESS")
queued = sum(1 for r in rows if r["state"] == "SCHEDULED")
print(
    "total=%d  %s first=%d  %s short=%d  in_progress=%d  scheduled=%d"
    % (
        len(rows),
        KIND_MARK["first"],
        first,
        KIND_MARK["short"],
        short,
        in_prog,
        queued,
    )
)
rabbit_path = Path(sys.argv[1]) / "rabbit.tsv"
ready_total = 0
unacked_total = 0
if rabbit_path.exists():
    for line in rabbit_path.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        if not parts[0].startswith("report-imports"):
            continue
        try:
            ready_total += int(parts[1])
            unacked_total += int(parts[2])
        except ValueError:
            continue
lost_scheduled = queued - ready_total
if lost_scheduled > 0:
    print(
        "WARNING: %d SCHEDULED in MariaDB but Rabbit ready=%d unacked=%d. "
        "%d waiting jobs have no queue message (AMQP TTL or orphan ack) "
        "and will not start until republished."
        % (queued, ready_total, unacked_total, lost_scheduled)
    )
PY

printf '\n=== Failed imports (open only, last 5) ===\n'
failed_imports="$(mysql_value "
        SELECT COUNT(*)
        FROM reportimport ri
        JOIN cloudaccount ca ON ca.id = ri.cloud_account_id
        WHERE ri.deleted_at = 0
            AND (${open_failed_predicate})
            ${organization_filter};")"

if [[ "$failed_imports" == 0 ]]; then
        echo "No open failed imports for this organization."
else
        if [[ "$failed_imports" -gt 5 ]]; then
                printf 'Showing 5 of %s open failures.\n' "$failed_imports"
        fi
        mysql_query "
                SELECT
                        ca.name AS connector,
                        ca.type AS connector_type,
                        DATE_FORMAT(
                            CONVERT_TZ(FROM_UNIXTIME(ri.created_at), '+00:00', '${MYSQL_TZ_OFFSET}'),
                            '%Y-%m-%d %H:%i:%s'
                        ) AS created,
                        LEFT(COALESCE(NULLIF(ri.state_reason, ''), 'No failure reason recorded'), 180) AS reason
                FROM reportimport ri
                JOIN cloudaccount ca ON ca.id = ri.cloud_account_id
                WHERE ri.deleted_at = 0
                    AND (${open_failed_predicate})
                    ${organization_filter}
                ORDER BY ri.created_at DESC
                LIMIT 5;"
fi

if [[ "$SHOW_DETAILS" == true ]]; then
    printf '\n=== Active and queued import details (up to %s) ===\n' "$LIMIT"
    mysql_query "
        SELECT
            ri.id,
            COALESCE(parent.name, ca.name) AS connector,
            COALESCE(parent.type, ca.type) AS connector_type,
            ca.name AS source,
            ri.state,
            ri.is_recalculation AS reimport,
            DATE_FORMAT(
                CONVERT_TZ(FROM_UNIXTIME(ri.created_at), '+00:00', '${MYSQL_TZ_OFFSET}'),
                '%Y-%m-%d %H:%i:%s'
            ) AS created,
            CASE
                WHEN ri.updated_at = 0 THEN '-'
                ELSE DATE_FORMAT(
                    CONVERT_TZ(FROM_UNIXTIME(ri.updated_at), '+00:00', '${MYSQL_TZ_OFFSET}'),
                    '%Y-%m-%d %H:%i:%s'
                )
            END AS updated,
            CASE
                WHEN ri.updated_at = 0 THEN '-'
                ELSE CONCAT(UNIX_TIMESTAMP() - ri.updated_at, 's')
            END AS since_update
        FROM reportimport ri
        JOIN cloudaccount ca ON ca.id = ri.cloud_account_id
        LEFT JOIN cloudaccount parent ON parent.id = ca.parent_id
        WHERE ri.deleted_at = 0
          AND ri.state IN ('IN_PROGRESS', 'SCHEDULED')
                    ${organization_filter}
        ORDER BY FIELD(ri.state, 'IN_PROGRESS', 'SCHEDULED'), ri.created_at ASC
        LIMIT ${LIMIT};"
fi

printf '\n=== Pool rules remapping ===\n'
# Active remaps only: a live python /tmp/apply_<job>_rules.py process.
# Optional force of progress tables: REMAP_POOL_ID=<project-pool-uuid>
# Convention:
#   script: /tmp/apply_<job>_rules.py  (optional POOL_ID=...)
#   log:    /tmp/apply_<job>_rules.log
remap_probe="$(docker compose exec -T restapi bash -lc '
running_job=""
for p in /proc/[0-9]*; do
  cmd=$(tr "\0" " " <"$p/cmdline" 2>/dev/null || true)
  case "$cmd" in
    *"/tmp/apply_"*"_rules.py"*)
      case "$cmd" in
        *"for p in /proc"*|*"running_job="*|*"remap_probe"*) continue ;;
      esac
      case "$cmd" in
        *python*|*python3*)
          running_job=$(printf "%s\n" "$cmd" | sed -n "s|.*apply_\([^ /]*\)_rules\.py.*|\1|p" | head -1)
          running_job=${running_job:-unknown}
          break
          ;;
      esac
      ;;
  esac
done
echo "running_job=${running_job}"

script=""
log=""
if [[ -n "$running_job" ]]; then
  script="/tmp/apply_${running_job}_rules.py"
  log="/tmp/apply_${running_job}_rules.log"
fi
if [[ -z "$log" || ! -f "$log" ]]; then
  log=$(ls -1t /tmp/apply_*_rules.log 2>/dev/null | head -1 || true)
fi
if [[ -z "$script" || ! -f "$script" ]]; then
  if [[ -n "$log" ]]; then
    base=$(basename "$log")
    job_from_log=${base#apply_}
    job_from_log=${job_from_log%_rules.log}
    script="/tmp/apply_${job_from_log}_rules.py"
  fi
fi

pool_id=""
if [[ -n "$script" && -f "$script" ]]; then
  echo "script_path=${script}"
  pool_id=$(sed -n "s/^POOL_ID *= *[\"'"'"']\\([^\"'"'"']*\\)[\"'"'"'].*/\\1/p" "$script" | head -1)
fi
if [[ -n "$log" && -f "$log" ]]; then
  echo "log_path=${log}"
  echo "log_bytes=$(wc -c <"$log")"
  base=$(basename "$log")
  job_from_log=${base#apply_}
  job_from_log=${job_from_log%_rules.log}
  echo "log_job=${job_from_log}"
  log_pool=$(sed -n "s/^POOL_ID=//p" "$log" | head -1)
  if [[ -n "$log_pool" ]]; then
    pool_id="$log_pool"
  fi
  if [[ -n "$running_job" ]]; then
    tail -n 200 "$log"
  fi
else
  echo "log_missing=1"
fi
if [[ -n "$pool_id" ]]; then
  echo "pool_id=${pool_id}"
fi
' 2>/dev/null || echo "running_job=
log_missing=1")"

remap_running_job="$(printf '%s\n' "$remap_probe" | awk -F= '/^running_job=/{print substr($0,13); exit}')"
remap_pool_id="$(printf '%s\n' "$remap_probe" | awk -F= '/^pool_id=/{print substr($0,9); exit}')"
if [[ -z "$remap_pool_id" && -n "${REMAP_POOL_ID:-}" ]]; then
    remap_pool_id="$REMAP_POOL_ID"
fi

# Idle path: no live apply job and no forced focus — skip Mongo (was ~1min org-wide).
if [[ -z "$remap_running_job" && -z "${REMAP_POOL_ID:-}" ]]; then
    printf 'status: idle\n'
    printf 'hint: progress tables render only while /tmp/apply_<job>_rules.py is running\n'
    printf 'hint: force with REMAP_POOL_ID=<project-pool-uuid>\n'
else
pool_purpose_tsv="$(mysql_value "
    SELECT purpose, COUNT(*)
    FROM pool
    WHERE organization_id = '${ORGANIZATION_ID}'
      AND deleted_at = 0
    GROUP BY purpose
    ORDER BY COUNT(*) DESC, purpose ASC;" 2>/dev/null || true)"

# Prefer the job's project pool; otherwise all service remaps (slower).
if [[ -n "$remap_pool_id" ]]; then
    remap_parent_filter="AND parent.id = '${remap_pool_id}'"
else
    remap_parent_filter=""
fi

remap_rules_tsv="$(mysql_value "
    SELECT
        parent.name,
        parent.id,
        child.name,
        child.id,
        MAX(CASE WHEN c.type = 'CLOUD_IS' THEN c.meta_info END),
        MAX(CASE WHEN c.type = 'TAG_IS' THEN c.meta_info END)
    FROM rule r
    JOIN pool child ON child.id = r.pool_id AND child.deleted_at = 0
    JOIN pool parent ON parent.id = child.parent_id AND parent.deleted_at = 0
    JOIN \`condition\` c ON c.rule_id = r.id AND c.deleted_at = 0
    WHERE r.organization_id = '${ORGANIZATION_ID}'
      AND r.deleted_at = 0
      AND r.active = 1
      AND child.purpose = 'ASSET_POOL'
      ${remap_parent_filter}
    GROUP BY parent.id, parent.name, child.id, child.name
    HAVING MAX(CASE WHEN c.type = 'TAG_IS' THEN c.meta_info END) IS NOT NULL
       AND MAX(CASE WHEN c.type = 'CLOUD_IS' THEN c.meta_info END) IS NOT NULL
    ORDER BY parent.name, child.name;" 2>/dev/null || true)"

remap_counts_tsv=""
if [[ -n "$remap_rules_tsv" && -n "$mongo_user" && -n "$mongo_password" ]]; then
    printf '%s\n' "$remap_rules_tsv" >"${tmp_dir}/remap_rules.tsv"
    remap_counts_tsv="$(
        MONGO_USER="${mongo_user}" MONGO_PASSWORD="${mongo_password}" \
        COMPOSE_DIR="${SCRIPT_DIR}" \
        python3 - "${tmp_dir}/remap_rules.tsv" <<'PY'
import base64, json, os, subprocess, sys
from collections import defaultdict
from pathlib import Path

by_parent = defaultdict(list)
for line in Path(sys.argv[1]).read_text().splitlines():
    if not line.strip():
        continue
    parts = line.split("\t")
    if len(parts) < 6:
        continue
    project, parent_id, name, child_id, cloud_id, tag_meta = parts[:6]
    try:
        meta = json.loads(tag_meta)
    except Exception:
        continue
    if meta.get("key") != "service" or not meta.get("value"):
        continue
    by_parent[parent_id].append({
        "project": project,
        "parent_id": parent_id,
        "name": name,
        "target": child_id,
        "cloud": cloud_id,
        "service": meta["value"],
    })

tag_key = base64.b64encode(b"service").decode("ascii")
mongo_user = os.environ["MONGO_USER"]
mongo_password = os.environ["MONGO_PASSWORD"]
compose_dir = os.environ["COMPOSE_DIR"]
counts = {}

for parent_id, prules in by_parent.items():
    services = sorted({r["service"] for r in prules})
    clouds = sorted({r["cloud"] for r in prules})
    pools = sorted({parent_id, *[r["target"] for r in prules]})
    js = "\n".join([
        "var tagKey = %s;" % json.dumps(tag_key),
        "var services = %s;" % json.dumps(services),
        "var clouds = %s;" % json.dumps(clouds),
        "var pools = %s;" % json.dumps(pools),
        """
var match = {cloud_account_id: {$in: clouds}, pool_id: {$in: pools}};
match["tags." + tagKey] = {$in: services};
db.resources.aggregate([
  {$match: match},
  {$group: {_id: {svc: "$tags." + tagKey, pool: "$pool_id"}, n: {$sum: 1}}}
], {allowDiskUse: true}).forEach(function(r){
  print(r._id.svc + "\\t" + r._id.pool + "\\t" + r.n);
});
""",
    ])
    proc = subprocess.run(
        [
            "docker", "compose", "exec", "-T", "mongo",
            "mongo", "-u", mongo_user, "-p", mongo_password,
            "--authenticationDatabase", "admin", "--quiet", "restapi",
        ],
        input=js.encode(),
        cwd=compose_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        continue
    for line in proc.stdout.decode().splitlines():
        if "\t" not in line:
            continue
        parts = line.split("\t")
        if len(parts) < 3 or not parts[2].isdigit():
            continue
        key = (parts[0], parts[1])
        counts[key] = counts.get(key, 0) + int(parts[2])

for (svc, pool), n in sorted(counts.items()):
    print("%s\t%s\t%s" % (svc, pool, n))
PY
    )" || true
fi

printf '%s\n' "$remap_probe" >"${tmp_dir}/remap.log"
printf '%s\n' "$pool_purpose_tsv" >"${tmp_dir}/pool_purpose.tsv"
printf '%s\n' "$remap_rules_tsv" >"${tmp_dir}/remap_rules.tsv"
printf '%s\n' "$remap_counts_tsv" >"${tmp_dir}/remap_counts.tsv"
python3 - "${tmp_dir}/remap.log" "${tmp_dir}/pool_purpose.tsv" \
    "${tmp_dir}/remap_rules.tsv" "${tmp_dir}/remap_counts.tsv" "$remap_pool_id" <<'PY'
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

raw = Path(sys.argv[1]).read_text().splitlines()
purpose_lines = Path(sys.argv[2]).read_text().splitlines() if len(sys.argv) > 2 else []
rules_lines = Path(sys.argv[3]).read_text().splitlines() if len(sys.argv) > 3 else []
counts_lines = Path(sys.argv[4]).read_text().splitlines() if len(sys.argv) > 4 else []
focus_pool = (sys.argv[5] if len(sys.argv) > 5 else "").strip()

running_job = ""
log_job = ""
log_path = ""
log_missing = False
job_pool_id = focus_pool
log_lines = []
for line in raw:
    if line.startswith("running_job="):
        running_job = line.split("=", 1)[1].strip()
        continue
    if line.startswith("log_job="):
        log_job = line.split("=", 1)[1].strip()
        continue
    if line.startswith("log_path="):
        log_path = line.split("=", 1)[1].strip()
        continue
    if line.startswith("log_missing="):
        log_missing = True
        continue
    if line.startswith("pool_id="):
        job_pool_id = line.split("=", 1)[1].strip() or job_pool_id
        continue
    if line.startswith("log_bytes=") or line.startswith("script_path="):
        continue
    log_lines.append(line)

purposes = []
for line in purpose_lines:
    if not line.strip():
        continue
    parts = line.split("\t")
    if len(parts) >= 2 and parts[1].isdigit():
        purposes.append((parts[0], int(parts[1])))

job_label = running_job or log_job or "none"

batch_total = None
unit_label = "pools"
batch_done = 0
last = None
apply_done = None
processed_sum = 0
updated_sum = 0
fail = 0
header_m = re.compile(r"Applying rules for (\d+)\s*(.*)$")
row_m = re.compile(
    r"^\[(\d+)/(\d+)\]\s+(.+?)\s+->\s+(\d+)\s+(\{.*\}|.*)$"
)
exc_m = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+?)\s+EXC\s+")
done_m = re.compile(r"^APPLY_DONE\s+ok=(\d+)\s+fail=(\d+)")

for line in log_lines:
    m = header_m.search(line)
    if m:
        batch_total = int(m.group(1))
        label = (m.group(2) or "").strip() or "pools"
        unit_label = label
        continue
    m = done_m.search(line)
    if m:
        apply_done = (int(m.group(1)), int(m.group(2)))
        continue
    m = row_m.match(line)
    if m:
        idx, tot, name, code, payload = m.groups()
        batch_done = max(batch_done, int(idx))
        batch_total = int(tot)
        last = (int(idx), name, int(code), payload.strip())
        pm = re.search(r"'processed':\s*(\d+)", payload)
        um = re.search(r"'updated_assignments':\s*(\d+)", payload)
        if pm:
            processed_sum += int(pm.group(1))
        if um:
            updated_sum += int(um.group(1))
        if int(code) >= 400:
            fail += 1
        continue
    m = exc_m.match(line)
    if m:
        batch_done = max(batch_done, int(m.group(1)))
        batch_total = int(m.group(2))
        last = (int(m.group(1)), m.group(3), -1, "exception")
        fail += 1

rules = []
for line in rules_lines:
    if not line.strip():
        continue
    parts = line.split("\t")
    if len(parts) < 6:
        continue
    project, parent_id, name, target_pool, cloud_id, tag_meta = parts[:6]
    try:
        meta = json.loads(tag_meta)
    except Exception:
        continue
    if meta.get("key") != "service" or not meta.get("value"):
        continue
    rules.append({
        "project": project,
        "parent_id": parent_id,
        "name": name,
        "target_pool": target_pool,
        "cloud_id": cloud_id,
        "service": meta["value"],
    })

counts = {}
for line in counts_lines:
    if not line.strip():
        continue
    parts = line.split("\t")
    if len(parts) < 3 or not parts[2].isdigit():
        continue
    counts[(parts[0], parts[1])] = int(parts[2])


def ascii_table(headers, rows):
    widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        cells = [str(c) for c in row]
        str_rows.append(cells)
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"

    def fmt(cells):
        return "| " + " | ".join(
            cells[i].ljust(widths[i]) for i in range(len(headers))
        ) + " |"

    print(sep)
    print(fmt(headers))
    print(sep)
    for cells in str_rows:
        print(fmt(cells))
    print(sep)


rule_rows = []
projects = defaultdict(lambda: {"done": 0, "pending": 0, "total": 0, "name": ""})
for rule in rules:
    svc = rule["service"]
    parent_id = rule["parent_id"]
    on_target = counts.get((svc, rule["target_pool"]), 0)
    on_source = counts.get((svc, parent_id), 0)
    total = on_target + on_source
    if total == 0:
        continue
    row = {
        "project": rule["project"],
        "parent_id": parent_id,
        "service": svc,
        "pool": rule["name"],
        "done": on_target,
        "pending": on_source,
        "total": total,
    }
    rule_rows.append(row)
    proj = projects[parent_id]
    proj["name"] = rule["project"]
    proj["done"] += on_target
    proj["pending"] += on_source
    proj["total"] += total

res_done = sum(p["done"] for p in projects.values())
res_total = sum(p["total"] for p in projects.values())
still_on_source = sum(p["pending"] for p in projects.values())

if apply_done is not None and not running_job:
    status = "done"
elif running_job:
    status = "running"
elif still_on_source == 0 and res_total > 0:
    status = "complete"
else:
    status = "idle"

print(f"status: {status}")
print(f"job: {job_label}")
if log_path:
    print(f"log: {log_path}")
if job_pool_id:
    print(f"focus_pool_id: {job_pool_id}")

if purposes:
    print("pools_by_purpose: " + ", ".join(f"{p}={n}" for p, n in purposes))

if res_total > 0:
    pct = 100.0 * res_done / res_total
    print(f"progress: {res_done}/{res_total} resources ({pct:.1f}%)")
    print(f"still_on_source_pool: {still_on_source}")
else:
    print("progress: no tagged service resources found for remap rules")

print(f"this_run_processed: {processed_sum}")
print(f"this_run_updated_assignments: {updated_sum}")
if fail:
    print(f"failures: {fail}")
if last:
    idx, name, code, payload = last
    code_s = "EXC" if code < 0 else str(code)
    print(f"last: [{idx}] {name} -> {code_s} {payload}")
if apply_done is not None:
    print(f"APPLY_DONE ok={apply_done[0]} fail={apply_done[1]}")
elif running_job and res_total > 0 and still_on_source > 0:
    print(f"remaining_resources: {still_on_source}")
elif running_job and res_total > 0 and still_on_source == 0:
    print("note: resource remaps look complete; waiting for rules_apply HTTP to finish")
elif running_job and batch_total and batch_done < batch_total:
    print(f"remaining: {batch_total - batch_done} {unit_label}")

if projects:
    print("")
    print("by_project (still pending remaps only):")
    proj_rows = []
    for parent_id, p in projects.items():
        if p["pending"] <= 0:
            continue
        ppct = 100.0 * p["done"] / p["total"] if p["total"] else 0.0
        proj_rows.append((
            p["name"],
            p["done"],
            p["pending"],
            p["total"],
            f"{ppct:.1f}%",
        ))
    proj_rows.sort(key=lambda r: (-int(r[2]), -int(r[3]), r[0]))
    if proj_rows:
        ascii_table(
            ["project", "done", "pending", "total", "progress"],
            proj_rows,
        )
    else:
        print("(none — no resources left on source project pools)")

# Only rules still queued for remapping (resources remain on the source pool).
# Do not dump completed 100% rules from the rest of the org / project.
detail_rows = [r for r in rule_rows if r["pending"] > 0]
if job_pool_id:
    detail_rows = [r for r in detail_rows if r["parent_id"] == job_pool_id]
    print("")
    print(f"by_rule pending for focus pool {job_pool_id}:")
else:
    print("")
    print("by_rule (pending remaps only):")

detail_rows = sorted(
    detail_rows,
    key=lambda r: (-r["pending"], -r["total"], r["project"], r["service"]),
)
show = detail_rows[:40]
table_rows = []
for r in show:
    rpct = 100.0 * r["done"] / r["total"]
    table_rows.append((
        r["project"],
        r["service"],
        r["pool"],
        r["done"],
        r["pending"],
        r["total"],
        f"{rpct:.1f}%",
    ))
if table_rows:
    ascii_table(
        ["project", "service", "target_pool", "done", "pending", "total", "progress"],
        table_rows,
    )
    hidden = len(detail_rows) - len(show)
    if hidden > 0:
        print(f"... +{hidden} more pending rules")
elif running_job and res_total > 0 and still_on_source == 0:
    print("(no pending rules — remaps look complete; waiting for job HTTP to finish)")
elif not rules:
    print("hint: no active TAG_IS(service)+CLOUD_IS asset-pool rules found")
    print("hint: batch remaps should write /tmp/apply_<job>_rules.log with optional POOL_ID=")
else:
    print("(no pending rules in scope)")
PY
fi

printf '\n=== Virtual tag re-apply ===\n'
# Few marker lines per apply (every 10k resources or 10s), not per resource.
# restapi emits: started / progress / finished for org+quarter.
vt_apply_log="$(timeout 20 docker compose logs --since 6h --timestamps restapi 2>/dev/null \
    | grep -E 'Virtual tag apply (started|progress|finished) for org ' \
    || true)"
printf '%s\n' "$vt_apply_log" >"${tmp_dir}/vt_apply.log"
python3 - "$tmp_dir/vt_apply.log" "$ORGANIZATION_ID" <<'PY'
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

log_path = Path(sys.argv[1])
org_id = sys.argv[2]
text = log_path.read_text() if log_path.exists() else ""

event_re = re.compile(
    r"Virtual tag apply (started|progress|finished) for org "
    r"([0-9a-f-]+) quarter (\d{4}Q[1-4]):(?: total=(\d+)|"
    r" processed=(\d+)(?: total=(\d+) pct=(\d+))?)"
)
# compose: "restapi-1  | 2026-08-25T09:38:51.570130237Z INFO:..."
ts_re = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(?:Z|[+-]\d{2}:\d{2})"
)


def parse_ts(raw):
    tm = ts_re.search(raw)
    if not tm:
        return None
    frac = (tm.group(2) or "0")[:6].ljust(6, "0")
    try:
        return datetime.fromisoformat("%s.%s+00:00" % (tm.group(1), frac))
    except ValueError:
        return None


def fmt_elapsed(seconds):
    if seconds is None or seconds < 0:
        return "-"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    if minutes:
        return "%dm %02ds" % (minutes, secs)
    return "%ds" % secs


events = []
for raw in text.splitlines():
    m = event_re.search(raw)
    if not m:
        continue
    kind, org, quarter = m.group(1), m.group(2), m.group(3)
    if org != org_id:
        continue
    started_total = int(m.group(4)) if m.group(4) else None
    processed = int(m.group(5)) if m.group(5) else None
    total = int(m.group(6)) if m.group(6) else started_total
    pct = int(m.group(7)) if m.group(7) else None
    events.append({
        "kind": kind,
        "quarter": quarter,
        "processed": processed,
        "total": total,
        "pct": pct,
        "ts": parse_ts(raw),
    })

if not events:
    print("status: idle")
    print("hint: no Virtual tag apply lines in restapi logs (6h)")
    raise SystemExit(0)

runs = {}
for ev in events:
    quarter = ev["quarter"]
    if ev["kind"] == "started" or quarter not in runs:
        runs[quarter] = {"start": ev, "last": ev}
    else:
        runs[quarter]["last"] = ev

now = datetime.now(timezone.utc)
rows = []
any_running = False
for quarter in sorted(runs, reverse=True):
    start_ev = runs[quarter]["start"]
    ev = runs[quarter]["last"]
    running = ev["kind"] in ("started", "progress")
    any_running = any_running or running
    processed = ev["processed"]
    total = ev["total"] if ev["total"] is not None else start_ev["total"]
    if processed is None:
        processed = 0 if ev["kind"] == "started" else None
    if ev["kind"] == "started" and total is not None:
        processed = processed or 0
    if ev["pct"] is not None:
        progress = "%s%%" % ev["pct"]
    elif total not in (None, 0) and processed is not None:
        progress = "%.1f%%" % (100.0 * processed / total)
    elif ev["kind"] == "finished":
        progress = "100%"
    else:
        progress = "-"
    start_ts = start_ev["ts"]
    end_ts = now if running else ev["ts"]
    elapsed = "-"
    if start_ts is not None and end_ts is not None:
        elapsed = fmt_elapsed((end_ts - start_ts).total_seconds())
    rows.append((
        "🟡" if running else "🟢",
        quarter,
        str(processed if processed is not None else "-"),
        str(total if total is not None else "-"),
        progress,
        "running" if running else "finished",
        elapsed,
    ))

print("status: running" if any_running else "status: idle")
headers = ("st", "quarter", "processed", "total", "progress", "state", "elapsed")
# Emoji is typically 2 terminal columns.
st_w = 2
widths = {h: (st_w if h == "st" else len(h)) for h in headers}
labeled = []
for row in rows:
    out = dict(zip(headers, row))
    labeled.append(out)
    for h in headers:
        if h == "st":
            continue
        widths[h] = max(widths[h], len(out[h]))

def pad(h, value):
    if h == "st":
        return value + (" " * max(0, st_w - 1))
    return value.ljust(widths[h])

def fmt(row):
    return "| " + " | ".join(pad(h, row[h]) for h in headers) + " |"

sep = "+-" + "-+-".join("-" * widths[h] for h in headers) + "-+"
print(sep)
print(fmt({h: h for h in headers}))
print(sep)
for row in labeled:
    print(fmt(row))
print(sep)
PY

printf '\n=== Host disk ===\n'
df -h / | awk 'NR==1 || NR==2'

printf '\nClean unused Docker logs (truncate JSON logs without restart):\n'
printf '  ./import-queue-status.sh --clean-logs   # skips diworker; may use sudo\n'
printf 'Also reclaim build cache / unused images when safe:\n'
printf '  docker builder prune -af\n'
printf '  docker image prune -af\n'

if [[ "$CLEAN_LOGS" == true ]]; then
    printf '\n'
    clean_docker_json_logs
fi

printf '\nUse --details to list individual active and queued imports.\n'
