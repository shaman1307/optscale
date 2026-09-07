#!/usr/bin/env bash
set -euo pipefail

# Build OptScale service images for docker-compose and tag them the way
# compose / docker-compose.override.yml expect: ${COMPANY}/<service>:local
#
# Root cause this fixes: ./build.sh <svc> local produces "<svc>:local", while
# compose looks for "hystax/<svc>:local" (and OPTSCALE_VERSION=local).
#
# Usage (from this directory or any cwd):
#   ./build-local.sh                    # ngui + rest_api + diworker
#   ./build-local.sh ngui               # one service
#   ./build-local.sh --all               # every first-party image in docker-compose.yml
#   ./build-local.sh --no-cache         # rebuild without cache
#   ./build-local.sh --no-up             # build without deploying
#   ./build-local.sh --no-prune          # keep build cache and unused images
#   ./build-local.sh rest_api diworker --no-cache
#
# Env overrides:
#   COMPANY=hystax TAG=local PROGRESS_INTERVAL=30

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

env_value() {
    local name="$1"
    local default="$2"
    if [[ -f "$ENV_FILE" ]]; then
        local value
        value="$(awk -F= -v name="$name" '
            $1 == name {
                value = substr($0, length(name) + 2)
            }
            END { if (value != "") printf "%s", value }
        ' "$ENV_FILE")"
        if [[ -n "${value}" ]]; then
            printf "%s" "$value"
            return
        fi
    fi
    printf "%s" "$default"
}

COMPANY="${COMPANY:-$(env_value "COMPANY" "hystax")}"
TAG="${TAG:-local}"
PROGRESS_INTERVAL="${PROGRESS_INTERVAL:-30}"
NO_CACHE=false
DO_UP=true
DO_ALL=false
DO_PRUNE=true
SERVICES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-cache) NO_CACHE=true ;;
        --up) DO_UP=true ;;
        --no-up) DO_UP=false ;;
        --no-prune) DO_PRUNE=false ;;
        --all) DO_ALL=true ;;
        -h|--help)
            sed -n '2,21p' "$0"
            exit 0
            ;;
        -*)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
        *)
            SERVICES+=("$1")
            ;;
    esac
    shift
done

# Build every first-party image that compose pulls from ${COMPANY}. The image
# names match their Dockerfile directory names, so they can be passed directly
# to build.sh.
if [[ "$DO_ALL" == true ]]; then
    mapfile -t SERVICES < <(
        sed -nE 's#^[[:space:]]*image: \$\{COMPANY:-hystax\}/([^:]+):.*#\1#p' \
            "$SCRIPT_DIR/docker-compose.yml" | sort -u
    )
elif [[ ${#SERVICES[@]} -eq 0 ]]; then
    # Default: services overridden to :local in docker-compose.override.yml
    # (Snowflake UI + API + import path).
    SERVICES=(ngui rest_api diworker)
fi

TOTAL_SERVICES=${#SERVICES[@]}

BUILD_FLAGS=()
if [[ "$NO_CACHE" == true ]]; then
    BUILD_FLAGS+=(--no-cache)
fi

# Map image name -> compose service names for --up.
# One image may back several services (organization_violations ->
# organization-violations-worker + organization-violations-scheduler).
compose_services_for() {
    local image="$1"
    awk -v image="$image" '
        BEGIN { target = "/" image ":" }
        /^services:[[:space:]]*$/ { in_services = 1; next }
        in_services && /^  [A-Za-z0-9_-]+:[[:space:]]*$/ {
            svc = $1
            sub(/:$/, "", svc)
            next
        }
        in_services && $1 == "image:" && index($0, target) {
            print svc
        }
    ' "$SCRIPT_DIR/docker-compose.yml"
}

append_compose_services() {
    local image="$1"
    local mapped=()
    mapfile -t mapped < <(compose_services_for "$image")
    if [[ ${#mapped[@]} -eq 0 ]]; then
        echo "ERROR: no compose service uses image ${image}" >&2
        exit 1
    fi
    COMPOSE_SERVICES+=("${mapped[@]}")
}

# build.sh / buildx sometimes finishes "DONE" a beat before the local tag is
# visible to `docker image inspect` (attestation/manifest unpack). Retry so
# build-local does not fail spuriously, and never drop the plain tag until
# the compose-prefixed tag is confirmed.
wait_for_image() {
    local ref="$1"
    local attempts="${2:-30}"
    local i
    for ((i = 1; i <= attempts; i++)); do
        if docker image inspect "$ref" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

dump_image_hint() {
    local plain="$1"
    local prefixed="$2"
    local svc_name="${plain%%:*}"
    echo "ERROR: expected image ${plain} (or ${prefixed}) after build.sh" >&2
    echo "docker images (matching ${svc_name}):" >&2
    docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedSince}}' \
        | grep -F "$svc_name" >&2 || true
}

ensure_optscale_version_local() {
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "No ${ENV_FILE} — skip OPTSCALE_VERSION update (copy .env.example first)."
        return
    fi
    local tmp
    tmp="$(mktemp)"
    awk -F= -v tag="$TAG" '
        BEGIN { found = 0 }
        $1 == "OPTSCALE_VERSION" {
            print "OPTSCALE_VERSION=" tag
            found = 1
            next
        }
        { print }
        END {
            if (!found) print "OPTSCALE_VERSION=" tag
        }
    ' "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
    echo "Set OPTSCALE_VERSION=${TAG} in .env"
}

print_progress() {
    local built=0
    local svc
    local prefixed
    local built_services=()

    for svc in "${SERVICES[@]}"; do
        prefixed="${COMPANY}/${svc}:${TAG}"
        if docker image inspect "$prefixed" >/dev/null 2>&1; then
            built=$((built + 1))
            built_services+=("$prefixed")
        fi
    done

    local percent=$((built * 100 / TOTAL_SERVICES))
    echo ""
    echo "════════════════════════════════════════════"
    printf 'Build progress: %d/%d (%d%%) local images\n' "$built" "$TOTAL_SERVICES" "$percent"
    echo "Built images:"
    if [[ ${#built_services[@]} -eq 0 ]]; then
        echo "  (none)"
    else
        printf '  %s\n' "${built_services[@]}"
    fi
}

PROGRESS_MONITOR_PID=""

stop_progress_monitor() {
    if [[ -n "$PROGRESS_MONITOR_PID" ]]; then
        kill "$PROGRESS_MONITOR_PID" 2>/dev/null || true
        wait "$PROGRESS_MONITOR_PID" 2>/dev/null || true
    fi
}

start_progress_monitor() {
    if ! [[ "$PROGRESS_INTERVAL" =~ ^[1-9][0-9]*$ ]]; then
        echo "ERROR: PROGRESS_INTERVAL must be a positive number of seconds" >&2
        exit 1
    fi

    (
        while sleep "$PROGRESS_INTERVAL"; do
            print_progress
        done
    ) &
    PROGRESS_MONITOR_PID=$!
    trap stop_progress_monitor EXIT
}

echo "Repo:    ${REPO_ROOT}"
echo "Company: ${COMPANY}"
echo "Tag:     ${TAG}"
echo "Build:   ${SERVICES[*]}"
echo "Prune:   $([[ "$DO_PRUNE" == true ]] && echo "builder+images after build" || echo "skipped")"
echo "Progress updates: every ${PROGRESS_INTERVAL}s"
echo "Commit:  $(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo ""

ensure_optscale_version_local
print_progress
start_progress_monitor

COMPOSE_SERVICES=()
for svc in "${SERVICES[@]}"; do
    plain="${svc}:${TAG}"
    prefixed="${COMPANY}/${svc}:${TAG}"

    if [[ "$DO_ALL" == true ]] && docker image inspect "$prefixed" >/dev/null 2>&1; then
        echo "Skipping ${svc}: ${prefixed} already exists"
        append_compose_services "$svc"
        continue
    fi

    echo "======== Building ${svc} ========"
    (
        cd "$REPO_ROOT"
        ./build.sh "${BUILD_FLAGS[@]}" "$svc" "$TAG"
    )

    # Prefer the unprefixed tag from build.sh; accept an already-prefixed
    # tag if a prior partial run left only that name.
    if wait_for_image "$plain" 30; then
        :
    elif wait_for_image "$prefixed" 5; then
        echo "Note: ${plain} missing; reusing existing ${prefixed}"
        plain="$prefixed"
    else
        dump_image_hint "$plain" "$prefixed"
        exit 1
    fi

    if [[ "$plain" != "$prefixed" ]]; then
        echo "Tagging ${plain} -> ${prefixed}"
        docker tag "$plain" "$prefixed"
        if ! wait_for_image "$prefixed" 10; then
            echo "ERROR: tagged ${prefixed} but inspect still fails" >&2
            dump_image_hint "$plain" "$prefixed"
            exit 1
        fi
        # Drop the unprefixed tag only after the compose tag is confirmed,
        # otherwise prune can race and leave nothing deployable.
        docker rmi "$plain" >/dev/null 2>&1 || true
    fi
    if ! wait_for_image "$prefixed" 5; then
        echo "ERROR: compose image ${prefixed} missing after build" >&2
        dump_image_hint "$plain" "$prefixed"
        exit 1
    fi
    append_compose_services "$svc"
done

stop_progress_monitor
trap - EXIT
print_progress

echo ""
echo "Built and tagged:"
for svc in "${SERVICES[@]}"; do
    echo "  ${COMPANY}/${svc}:${TAG}"
done

if [[ "$DO_UP" == true ]]; then
    echo ""
    if [[ "$DO_ALL" == true ]]; then
        echo "All ${TOTAL_SERVICES} local images were built successfully. Starting the complete compose stack."
        (
            cd "$SCRIPT_DIR"
            docker compose up -d --force-recreate
        )
    else
        echo "Recreating only the successfully built compose services: ${COMPOSE_SERVICES[*]}"
        (
            cd "$SCRIPT_DIR"
            docker compose up -d --no-deps --force-recreate "${COMPOSE_SERVICES[@]}"
        )
    fi
fi

if [[ "$DO_PRUNE" == true ]]; then
    echo ""
    echo "Cleaning Docker builder cache and unused images..."
    docker builder prune -af
    # After --up the new image is in use, so -a drops leftover build/base
    # images. Without --up keep the just-built tag (dangling only).
    if [[ "$DO_UP" == true ]]; then
        docker image prune -af
    else
        docker image prune -f
    fi
fi

echo ""
echo "Done. Verify, e.g.:"
echo "  docker image ls '${COMPANY}/*:${TAG}'"
echo "  docker compose -f ${SCRIPT_DIR}/docker-compose.yml config | grep -E 'ngui|rest_api|diworker'"
