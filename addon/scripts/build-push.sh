#!/usr/bin/env bash
#
# Build and push the Kilometerregistratie add-on images to a registry (e.g. Docker Hub),
# so Home Assistant can PULL them instead of building locally.
#
# HA add-ons are per-architecture: the config.yaml `image:` key uses {arch}, and HA
# pulls `<repo>-<arch>:<version>`. This script builds and pushes one image per arch.
#
# Usage:
#   DOCKERHUB_USER=youruser ./scripts/build-push.sh [version]
#
# Requires: docker login already done (`docker login -u youruser`).
# The preferred path uses the official Home Assistant builder (multi-arch, correct
# labels + tags). A plain `docker buildx` fallback is provided for amd64/aarch64/armv7.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

: "${DOCKERHUB_USER:?Set DOCKERHUB_USER=youruser}"
VERSION="${1:-$(grep '^version:' config.yaml | head -1 | sed 's/version:[[:space:]]*//; s/"//g')}"

echo "Repo:    ${DOCKERHUB_USER}/kilometerregistratie-{arch}"
echo "Version: ${VERSION}"

use_ha_builder() {
  echo ">> Building with the official Home Assistant builder (all arches)"
  docker run --rm --privileged \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "${REPO_ROOT}":/data \
    ghcr.io/home-assistant/amd64-builder \
      --all \
      -t /data \
      -r "${DOCKERHUB_USER}" \
      --docker-hub "${DOCKERHUB_USER}"
}

use_buildx_fallback() {
  echo ">> Falling back to docker buildx (per-arch)"
  declare -A PLATFORMS=(
    [amd64]="linux/amd64"
    [aarch64]="linux/arm64"
    [armv7]="linux/arm/v7"
  )
  declare -A BASES=(
    [amd64]="ghcr.io/home-assistant/amd64-base:3.20"
    [aarch64]="ghcr.io/home-assistant/aarch64-base:3.20"
    [armv7]="ghcr.io/home-assistant/armv7-base:3.20"
  )
  for arch in amd64 aarch64 armv7; do
    tag="${DOCKERHUB_USER}/kilometerregistratie-${arch}:${VERSION}"
    latest="${DOCKERHUB_USER}/kilometerregistratie-${arch}:latest"
    echo ">> ${arch} -> ${tag}"
    docker buildx build \
      --platform "${PLATFORMS[$arch]}" \
      --build-arg "BUILD_FROM=${BASES[$arch]}" \
      -t "${tag}" -t "${latest}" \
      --push .
  done
}

if docker image inspect ghcr.io/home-assistant/amd64-builder >/dev/null 2>&1 \
   || docker pull ghcr.io/home-assistant/amd64-builder >/dev/null 2>&1; then
  use_ha_builder
else
  use_buildx_fallback
fi

echo "Done. In config.yaml set:  image: \"${DOCKERHUB_USER}/kilometerregistratie-{arch}\""
echo "Then bump version and reinstall/update the add-on in Home Assistant."
