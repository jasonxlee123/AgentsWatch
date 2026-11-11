#!/usr/bin/env bash
set -euo pipefail

bundle="${1:-}"
if [[ -z "$bundle" ]]; then
  bundle="$(ls -1 dist/offline/agentswatch-images-linux-amd64-*.tar.gz 2>/dev/null | tail -1 || true)"
fi

if [[ -z "$bundle" || ! -f "$bundle" ]]; then
  echo "image bundle not found. Usage: scripts/load_offline_images.sh <agentswatch-images-linux-amd64-*.tar.gz>" >&2
  exit 1
fi

if [[ -f "${bundle}.sha256" ]]; then
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -c "${bundle}.sha256"
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c "${bundle}.sha256"
  fi
fi

docker load -i "$bundle"
echo "loaded offline images from $bundle"
