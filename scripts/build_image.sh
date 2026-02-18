#!/usr/bin/env bash
set -euo pipefail

REGISTRY="${REGISTRY:-ghcr.io}"
IMAGE_NAME="${IMAGE_NAME:-${USER}/agentai}"
TAG="${TAG:-local}"
PUSH=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      TAG="${2:?missing value for --tag}"
      shift 2
      ;;
    --push)
      PUSH=true
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

IMAGE_REF="${REGISTRY}/${IMAGE_NAME}:${TAG}"

if command -v podman >/dev/null 2>&1; then
  podman build -f Containerfile -t "${IMAGE_REF}" .
  if [[ "${PUSH}" == "true" ]]; then
    podman push "${IMAGE_REF}"
  fi
  exit 0
fi

if command -v buildah >/dev/null 2>&1; then
  buildah bud --isolation chroot -f Containerfile -t "${IMAGE_REF}" .
  if [[ "${PUSH}" == "true" ]]; then
    buildah push "${IMAGE_REF}"
  fi
  exit 0
fi

echo "podman or buildah is required to build images" >&2
exit 1
