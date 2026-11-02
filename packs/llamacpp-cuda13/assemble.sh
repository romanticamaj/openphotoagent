#!/usr/bin/env bash
# Assemble the llamacpp-cuda13 pack tree into $1 for platform $2 (08 §4.2). The upstream binaries are fetched and built only
# on the CI runner for that platform; this cloud workspace cannot (no GPU, no macOS/Windows, release downloads
# blocked), so the build itself is verified by the packs workflow, not locally.
set -euo pipefail
out="$1"; platform="$2"
mkdir -p "$out/bin"
echo "assemble llamacpp-cuda13 for $platform: see docs/runtime-packs.md" >&2
exit 1  # pending: upstream build recipe per platform (tracked in docs/BLOCKERS.md B-17)
