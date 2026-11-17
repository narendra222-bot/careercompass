#!/usr/bin/env bash
# Build the folder that is deployed to Vercel as the CareerCompass API.
# Usage: deploy/vercel-api/stage.sh <output-dir>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:?output dir}"
rm -rf "$OUT" && mkdir -p "$OUT/data" "$OUT/models"
cp -r "$ROOT/careercompass" "$OUT/"
find "$OUT" -name __pycache__ -prune -exec rm -rf {} +
cp "$ROOT"/data/*.yaml "$ROOT/data/resource_cache.json" "$ROOT/data/onet.duckdb" "$OUT/data/"
cp -r "$ROOT/data/chroma" "$OUT/data/"
cp "$ROOT/deploy/vercel-api/index.py" "$ROOT/deploy/vercel-api/vercel.json" "$ROOT/deploy/vercel-api/.python-version" "$OUT/"
# the packages the API imports, with the ONNX embedder instead of PyTorch
grep -vE '^(sentence-transformers|ortools|langchain-)' "$ROOT/requirements.txt" > "$OUT/requirements.txt"
echo 'fastembed==0.9.1' >> "$OUT/requirements.txt"
# the embedding model, so a cold start does not download it
FASTEMBED_CACHE_PATH="$OUT/models" "$ROOT/.venv/bin/python" -c \
  "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" >/dev/null
echo "staged in $OUT ($(du -sh "$OUT" | cut -f1))"
