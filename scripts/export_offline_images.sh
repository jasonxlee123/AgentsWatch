#!/usr/bin/env bash
set -euo pipefail

PLATFORM="${PLATFORM:-linux/amd64}"
TAG="${AGENTSWATCH_IMAGE_TAG:-$(date +%Y.%m.%d)}"
OUT_DIR="${OUT_DIR:-dist/offline}"
OUT_TAR="${OUT_DIR}/agentswatch-images-${PLATFORM//\//-}-${TAG}.tar"
OUT_GZ="${OUT_TAR}.gz"

mkdir -p "$OUT_DIR"

pull_image() {
  local source="$1"
  echo "==> pull ${source} (${PLATFORM})"
  docker pull --platform "$PLATFORM" "$source"
}

vendor_images=(
  "minio/minio:latest"
  "minio/mc:latest"
  "otel/opentelemetry-collector-contrib:latest"
  "prom/prometheus:latest"
  "prom/alertmanager:latest"
  "quay.io/thanos/thanos:v0.38.0"
  "grafana/loki:latest"
  "grafana/tempo:2.7.2"
  "grafana/grafana:latest"
  "nginx:stable-alpine"
  "clickhouse/clickhouse-server:latest"
)

for image in "${vendor_images[@]}"; do
  pull_image "$image"
done

images=(
  "${vendor_images[@]}"
)

printf "%s\n" "${images[@]}" > "${OUT_DIR}/images-${TAG}.txt"

echo "==> save images to ${OUT_TAR}"
rm -f "$OUT_TAR" "$OUT_GZ"
docker save "${images[@]}" -o "$OUT_TAR"

echo "==> gzip ${OUT_TAR}"
gzip -f "$OUT_TAR"

if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$OUT_GZ" > "${OUT_GZ}.sha256"
elif command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$OUT_GZ" > "${OUT_GZ}.sha256"
fi

echo "offline image bundle: ${OUT_GZ}"
echo "image list: ${OUT_DIR}/images-${TAG}.txt"
