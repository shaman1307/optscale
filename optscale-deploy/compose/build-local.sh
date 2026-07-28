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
#   ./build-local.sh --no-cache         # rebuild without cache
#   ./build-local.sh --up               # recreate matching compose services after build
#   ./build-local.sh rest_api diworker --no-cache --up
#
# Env overrides:
#   COMPANY=hystax TAG=local

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
NO_CACHE=false
DO_UP=false
SERVICES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-cache) NO_CACHE=true ;;
        --up) DO_UP=true ;;
        -h|--help)
            sed -n '2,20p' "$0"
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

# Default: services overridden to :local in docker-compose.override.yml
# (Snowflake UI + API + import path).
if [[ ${#SERVICES[@]} -eq 0 ]]; then
    SERVICES=(ngui rest_api diworker)
fi

BUILD_FLAGS=()
if [[ "$NO_CACHE" == true ]]; then
    BUILD_FLAGS+=(--no-cache)
fi

# Map image name -> compose service name for --up
compose_service_for() {
    case "$1" in
        rest_api) echo "restapi" ;;
        *) echo "$1" ;;
    esac
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

echo "Repo:    ${REPO_ROOT}"
echo "Company: ${COMPANY}"
echo "Tag:     ${TAG}"
echo "Build:   ${SERVICES[*]}"
echo "Commit:  $(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo ""

ensure_optscale_version_local

COMPOSE_SERVICES=()
for svc in "${SERVICES[@]}"; do
    echo "======== Building ${svc} ========"
    (
        cd "$REPO_ROOT"
        ./build.sh "${BUILD_FLAGS[@]}" "$svc" "$TAG"
    )

    plain="${svc}:${TAG}"
    prefixed="${COMPANY}/${svc}:${TAG}"
    if ! docker image inspect "$plain" >/dev/null 2>&1; then
        echo "ERROR: expected image ${plain} after build.sh, not found" >&2
        exit 1
    fi
    echo "Tagging ${plain} -> ${prefixed}"
    docker tag "$plain" "$prefixed"
    COMPOSE_SERVICES+=("$(compose_service_for "$svc")")
done

echo ""
echo "Built and tagged:"
for svc in "${SERVICES[@]}"; do
    echo "  ${COMPANY}/${svc}:${TAG}"
done

if [[ "$DO_UP" == true ]]; then
    echo ""
    echo "Recreating compose services: ${COMPOSE_SERVICES[*]}"
    (
        cd "$SCRIPT_DIR"
        docker compose up -d --force-recreate "${COMPOSE_SERVICES[@]}"
    )
fi

echo ""
echo "Done. Verify, e.g.:"
echo "  docker image ls '${COMPANY}/*:${TAG}'"
echo "  docker compose -f ${SCRIPT_DIR}/docker-compose.yml config | grep -E 'ngui|rest_api|diworker'"
