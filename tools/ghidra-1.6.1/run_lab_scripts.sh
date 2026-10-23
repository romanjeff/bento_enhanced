#!/usr/bin/env bash
# Run bento-community-lab's four read-only Ghidra report scripts headlessly.
# -readOnly: the project is never saved, whatever a script does.
# Their askFile() prompts are answered by the script argument (the report path).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd -W)"
GH="/c/Users/rjrom/tools/Ghidra/ghidra_12.1.4_PUBLIC/support/analyzeHeadless.bat"
LAB="C:/Users/rjrom/bento_mods/bento-community-lab/research/ghidra_scripts"
OUT="$HERE/lab_reports"; mkdir -p "$OUT"
export JAVA_HOME="C:/Program Files/Eclipse Adoptium/jdk-25.0.4.101-hotspot"
for s in Bento_16_Ghidra_Report Bento_16_Track_Trace Bento_16_Deep_Dive Bento_16_Full_Survey; do
  echo "== $s"
  "$GH" "$HERE" bento161 -process bento161 -noanalysis -readOnly \
    -scriptPath "$LAB" -postScript "$s.java" "$OUT/$s.txt" > "$OUT/$s.log" 2>&1 || echo "  FAILED, see $s.log"
  ls -la "$OUT/$s.txt" 2>/dev/null || echo "  no report"
done
