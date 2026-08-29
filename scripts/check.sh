#!/usr/bin/env bash
# Compile gate for AreaTrap. Two things have to hold and neither tool checks both.
#
#   1. The pure core must build with NO NinjaTrader assemblies at all — that is
#      what `dotnet run --project tests` proves, and it runs the assert suite
#      while it is there.
#   2. The NT8 files must build TOGETHER against NT8's references. `nt8c check`
#      is per-file and cannot see a sibling's types, so it reports CS0246 on
#      every cross-file reference. Concatenating them into one compilation unit
#      — usings hoisted to the top, where C# requires them — is the closest
#      thing to NT8's own F5 that runs from a shell.
#
# Adding a file to the strategy means adding one word to NT8_FILES.
#
# Usage: scripts/check.sh   (from anywhere)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/ninjascript"

# The combined compilation unit, in dependency order. AreaTrapCore has zero NT8
# references and the test project is its real gate, but it belongs in this list
# anyway: the strategy uses its types, and `nt8c check` on one file cannot see a
# sibling's, which is what makes the concatenation necessary in the first place.
NT8_FILES=(AreaTrapCore AreaTrapStrategy AreaTrapVision)

echo "== 1/2  pure core + assert suite"
dotnet run --project "$ROOT/tests" | tail -3

if [ ${#NT8_FILES[@]} -eq 0 ]; then
  echo ""
  echo "== 2/2  no NT8 files yet — checking the core compiles under nt8c anyway"
  nt8c check "$SRC/AreaTrapCore.cs" >/dev/null && echo "  AreaTrapCore.cs compiles clean"
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
COMBINED="$TMP/AreaTrapCombined.cs"

for f in "${NT8_FILES[@]}"; do grep -hE '^\s*using [A-Za-z]' "$SRC/$f.cs"; done \
  | sed 's/^\s*//' | sort -u > "$COMBINED"
for f in "${NT8_FILES[@]}"; do
  echo ""
  echo "// ===== $f.cs ====="
  grep -vE '^\s*using [A-Za-z]' "$SRC/$f.cs"
done >> "$COMBINED"

echo ""
echo "== 2/2  all NT8 files against NT8 references"
if nt8c check "$COMBINED" 2>&1 | tee "$TMP/out.txt" | grep -q "error CS"; then
  sed "s#$COMBINED#AreaTrapCombined.cs#" "$TMP/out.txt"
  exit 1
fi
echo "  compiles clean"
