#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

TARGETS=("$@")

echo "==> Validating Compose configuration..."
docker compose config --quiet

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  echo "==> Pulling latest upstream images (all stacks, including homeassistant)..."
  docker compose pull --ignore-buildable

  echo "==> Rebuilding local custom images (pulling latest base layers)..."
  docker compose build --pull

  echo "==> Recreating updated containers..."
  docker compose up -d --remove-orphans
else
  echo "==> Pulling latest upstream images for: ${TARGETS[*]}..."
  docker compose pull --ignore-buildable "${TARGETS[@]}"

  echo "==> Rebuilding any buildable target images..."
  docker compose build --pull "${TARGETS[@]}"

  echo "==> Recreating updated target containers..."
  docker compose up -d "${TARGETS[@]}"
fi

echo "==> Pruning unused dangling images and stale build cache..."
docker image prune -f
docker builder prune -f --filter "until=24h"

echo "==> Stack status:"
docker compose ps

echo "==> Host automation services:"
for unit in homeserver-updater homeserver-media-sorter homeserver-bluetooth-reconnect homeserver-vlc; do
  status="$(systemctl is-active "${unit}.service" 2>/dev/null || true)"
  printf "  %-30s %s\n" "${unit}" "${status:-unknown}"
done

echo "==> Root disk usage:"
df -h /
