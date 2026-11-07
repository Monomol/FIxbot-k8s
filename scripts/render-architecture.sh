#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

SRC_DIR="docs/architecture/src"
SVG_DIR="docs/architecture/svg"
PNG_DIR="docs/architecture/png"

if ! command -v dot >/dev/null 2>&1; then
  echo "error: graphviz is not installed (need 'dot')" >&2
  echo "hint: apk add --no-cache graphviz font-noto" >&2
  exit 1
fi

mkdir -p "$SVG_DIR" "$PNG_DIR"

for src in "$SRC_DIR"/*.dot; do
  name=$(basename "$src" .dot)
  echo "rendering $name"
  dot -Tsvg "$src" -o "$SVG_DIR/$name.svg"
  dot -Tpng -Gdpi=200 "$src" -o "$PNG_DIR/$name.png"
done

echo "composing poster"
python3 scripts/compose-architecture-poster.py

echo "done: $(ls "$SVG_DIR"/*.svg | wc -l) SVG + $(ls "$PNG_DIR"/*.png | wc -l) PNG"
