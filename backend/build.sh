#!/usr/bin/env bash
# Build the Lambda deployment asset consumed by SundialApp.
#
# Dependencies are resolved for the *target* platform, not this machine's:
# Lambda is ARM64/Graviton (§4.1) and pydantic ships compiled wheels, so
# building on an x86 laptop and hoping is how you get an ImportError at
# runtime rather than at build time.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/dist/lambda"
lock="$here/dist/requirements.txt"

rm -rf "$out"
mkdir -p "$out"

uv pip compile "$here/pyproject.toml" --quiet --output-file "$lock"

uv pip install \
  --target "$out" \
  --python-platform aarch64-manylinux2014 \
  --python-version 3.13 \
  --only-binary=:all: \
  --quiet \
  --requirement "$lock"

# boto3 and botocore are already in the Lambda runtime; shipping them again
# costs ~15MB of cold start for nothing.
rm -rf "$out"/boto3 "$out"/botocore "$out"/dateutil "$out"/s3transfer

cp -r "$here/src/sundial" "$out/sundial"

find "$out" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$out" -name "*.dist-info" -type d -prune -exec rm -rf {} +

echo "built $out ($(du -sh "$out" | cut -f1))"
