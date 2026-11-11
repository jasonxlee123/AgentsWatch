#!/usr/bin/env bash
# Build a publishable AgentsWatch release from the current HEAD tree.
#
# The private development repository keeps its full history. This script
# exports only the tracked tree into a fresh single-commit repository, so no
# historical object, local inventory, token, or ignored runtime directory can
# reach GitHub.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "${ROOT}/VERSION")"
DIST="${ROOT}/dist"
TARGET="${DIST}/agentswatch-${VERSION}-public"
ARCHIVE="${DIST}/agentswatch-${VERSION}-source.tar.gz"

fail() {
  echo "release build failed: $*" >&2
  exit 1
}

case "${VERSION}" in
  [0-9]*.[0-9]*.[0-9]*) ;;
  *) fail "VERSION must be a semantic version, got '${VERSION}'" ;;
esac

cd "${ROOT}"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  fail "working tree has uncommitted tracked changes; commit before building a release"
fi

echo "==> release hygiene (tracked files at HEAD)"
# The private development history is intentionally not scanned here; the new
# single-commit repository below is scanned with --history after it is built.
DENYLIST="${ROOT}/.release-denylist.local"
if [ -f "${DENYLIST}" ]; then
  python3 scripts/check_public_release.py --denylist "${DENYLIST}"
else
  echo "warning: private denylist not found; only generic rules were checked" >&2
  python3 scripts/check_public_release.py
fi

echo "==> exporting tracked tree at HEAD"
mkdir -p "${DIST}"
STAGING="$(mktemp -d "${DIST}/.release-staging.XXXXXX")"
trap 'rm -rf "${STAGING}"' EXIT
git archive --format=tar HEAD | tar -x -C "${STAGING}"

prohibited=(
  ".env"
  "cluster/lobsters.json"
  "prometheus/scrape_configs/openclaw-gateways.yml"
  "frontend/AGENTS.md"
  ".local-data"
  ".release-denylist.local"
)
for path in "${prohibited[@]}"; do
  if [ -e "${STAGING}/${path}" ]; then
    fail "prohibited path present in export: ${path}"
  fi
done
if find "${STAGING}" -type f \( -name '*.token' -o -name '*.key' -o -name '*.pem' -o -name '*.p12' -o -name '*.pfx' \) -print -quit | grep -q .; then
  fail "credential-shaped file present in export"
fi

if [ -e "${TARGET}" ]; then
  echo "==> replacing existing ${TARGET}"
  rm -rf "${TARGET}"
fi
mv "${STAGING}" "${TARGET}"
trap - EXIT

echo "==> creating single-commit public repository"
git -C "${TARGET}" init -q -b main
git -C "${TARGET}" config user.name "AgentsWatch Release"
git -C "${TARGET}" config user.email "release@agentswatch.invalid"
git -C "${TARGET}" add -A
git -C "${TARGET}" commit -q -m "release: AgentsWatch ${VERSION}"
git -C "${TARGET}" tag -a "${VERSION}" -m "AgentsWatch ${VERSION}"

echo "==> verifying public history"
python3 "${TARGET}/scripts/check_public_release.py" --history

echo "==> creating source archive"
git -C "${TARGET}" archive --format=tar.gz \
  --prefix="agentswatch-${VERSION}/" \
  --output="${ARCHIVE}" "${VERSION}"
( cd "${DIST}" && shasum -a 256 "$(basename "${ARCHIVE}")" > "$(basename "${ARCHIVE}").sha256" )

echo
echo "release ready:"
echo "  repository: ${TARGET}"
echo "  archive:    ${ARCHIVE}"
echo "  checksum:   ${ARCHIVE}.sha256"
git -C "${TARGET}" log --oneline --decorate
