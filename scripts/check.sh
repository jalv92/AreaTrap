#!/usr/bin/env bash
# Compile gate for AreaTrap. Two things have to hold and neither tool checks both.
#
#   1. The pure core must build with NO NinjaTrader assemblies at all — that is
#      what `dotnet run --project tests` proves, and it runs the assert suite
#      while it is there.
#   2. The NinjaScript files must build the way NinjaTrader actually builds them:
#      every .cs under Custom/ into ONE assembly, each file keeping ITS OWN using
#      directives. `nt8c build --custom-dir` does exactly that with NT8's own
#      compiler and reference set.
#
# WHY THIS IS NOT THE OBVIOUS `nt8c check` PER FILE, AND NOT A CONCATENATION.
# Both were tried here and both lie, in opposite directions:
#
#   * `nt8c check <file>` compiles one file alone, so every reference to a sibling
#     type is a false CS0246. AreaTrapStrategy alone reports 61 phantom errors.
#   * Concatenating the files into one unit fixes that but has to hoist all the
#     usings to the top, which SHARES them across files that never imported them.
#     Measured: adding `using System.Windows.Media;` to AreaTrapCore.cs — a file
#     with no rendering code at all — makes AreaTrapVision.cs fail with 10 CS0104
#     ambiguities that NinjaTrader would never report. A gate that invents errors
#     gets ignored, which is the same as not having one.
#
# AND THE BUG THIS FILE EXISTS TO NOT REPEAT. The previous version tested the
# compiler through a pipe:
#
#     if nt8c check "$COMBINED" 2>&1 | tee out.txt | grep -q "error CS"; then
#
# under `set -o pipefail`. `nt8c` exits 5 when it finds errors, so a FAILING
# compile made the pipeline non-zero, the condition FALSE, and the script printed
# "compiles clean". Worse, with no errors `grep -q` exits 1 and the pipeline is
# non-zero too — so the error branch could never run for ANY input. It shipped a
# file with 10 real errors straight to NinjaTrader. Never test a compiler through
# a pipe: capture, then read the exit code AND the output.
#
# Usage: scripts/check.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/ninjascript"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/Custom"

echo "== 1/2  pure core + assert suite"
dotnet run --project "$ROOT/tests" | tail -3

# Staged in the shape NT8 deploys them: the folder is decided by the namespace,
# not by the role, which is why the core sits with the strategies.
mkdir -p "$STAGE/Strategies" "$STAGE/Indicators"
cp "$SRC/AreaTrapCore.cs"     "$STAGE/Strategies/"
cp "$SRC/AreaTrapStrategy.cs" "$STAGE/Strategies/"
cp "$SRC/AreaTrapVision.cs"   "$STAGE/Indicators/"
EXPECTED=3

echo ""
echo "== 2/2  all NinjaScript files as one assembly, NT8-style"

set +e
nt8c build --custom-dir "$STAGE" --no-emit --agent > "$WORK/out.json" 2>&1
rc=$?
set -e

python3 - "$WORK/out.json" "$rc" "$EXPECTED" <<'PY'
import json, sys
raw = open(sys.argv[1]).read()
rc, expected = int(sys.argv[2]), int(sys.argv[3])
try:
    d = json.loads(raw)
except Exception:
    # A hard nt8c failure prints a bare `error: ...` line, not JSON. Treating an
    # unparseable body as success is how a gate goes quietly blind.
    print(raw.strip())
    print("  FAILED (nt8c exit %d, no JSON)" % rc)
    sys.exit(1)

errs = d.get("results", {}).get("errors", []) or []
n = d.get("meta", {}).get("files_compiled", 0)

# Only OUR files are staged, so every error here is ours -- no ownership filter to
# keep in sync, and therefore no blind spot for a file someone adds later.
print("  files compiled: %s   errors: %d" % (n, len(errs)))
for e in errs[:40]:
    print("    %s(%s,%s): %s %s" % (e.get("file", "?").split("/")[-1], e.get("line"),
                                    e.get("col"), e.get("code"), e.get("message")))

if n != expected:
    print("  FAILED: staged %d files but nt8c compiled %s. A vacuous green is not a pass."
          % (expected, n))
    sys.exit(1)
if errs:
    print("  FAILED (nt8c exit %d)" % rc)
    sys.exit(1)
print("  compiles clean")
PY
