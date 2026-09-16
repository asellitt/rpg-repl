#!/usr/bin/env bash
set -euo pipefail

BUMP="${1:-}"
IMAGE="asellitt/rpg-repl"

usage() {
  echo "Usage: $0 <patch|minor|major>"
  exit 1
}

[[ "$BUMP" =~ ^(patch|minor|major)$ ]] || usage

# The image is built from the working tree but the git tag marks HEAD --
# refuse to release unless they are the same thing.
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is dirty; commit or stash before releasing."
  exit 1
fi

# Find latest semver tag
LATEST=$(git tag --list 'v*.*.*' | sort -V | tail -1)

if [[ -z "$LATEST" ]]; then
  CURRENT="0.0.0"
else
  CURRENT="${LATEST#v}"
fi

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

case "$BUMP" in
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  patch) PATCH=$((PATCH + 1)) ;;
esac

NEXT="${MAJOR}.${MINOR}.${PATCH}"
TAG="v${NEXT}"

echo ""
echo "  Current: ${LATEST:-none}"
echo "  Next:    ${TAG}  →  ${IMAGE}:${NEXT}  +  ${IMAGE}:latest"
echo ""
read -r -p "Proceed? [y/N] " CONFIRM
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# Tag before publishing: a failed build leaves an unpushed local tag
# (delete with `git tag -d`), never a published image with no tag.
echo ""
echo "Tagging commit as ${TAG}..."
git tag -a "${TAG}" -m "${TAG}"

# Ensure buildx multi-arch builder exists
if ! docker buildx inspect rpg-repl-builder &>/dev/null; then
  docker buildx create --name rpg-repl-builder --use
else
  docker buildx use rpg-repl-builder
fi

echo ""
echo "Building and pushing ${IMAGE}:${NEXT} (linux/amd64 + linux/arm64)..."
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --label "org.opencontainers.image.version=${NEXT}" \
  --tag "${IMAGE}:${NEXT}" \
  --tag "${IMAGE}:latest" \
  --push \
  .

echo ""
echo "Pushing git tag ${TAG}..."
git push origin "${TAG}"
