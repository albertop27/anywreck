#!/bin/bash
# Wreck every mesh in a jobs file at several severities, in parallel. Run from the repo root.
#   pipeline/matrix.sh examples/props.jobs        jobs file: one "name|input mesh|profile|seed" per line
#   SEVS="0 0.3 0.6 0.9"  JOBS=6  WRECK_ARGS="--res 100"
# -> out/<name>/clean.blend, s30.blend/.glb/.json/.log, s60..., s90...
export B="${BLENDER:-/c/Program Files/Blender Foundation/Blender 5.2/blender.exe}"
one() {
  IFS='|' read -r name input profile seed sev <<< "$1"
  if [ "$sev" = "0" ]; then tag=clean; else tag=$(printf "s%02d" "$(awk "BEGIN{print int($sev*100+0.5)}")"); fi
  mkdir -p "out/$name"
  if [ "$sev" = "0" ]; then
    "$B" -b --factory-startup --python pipeline/wreck.py -- --input "$input" --output "out/$name/clean.blend" --severity 0 > "out/$name/clean.log" 2>&1
  else
    "$B" -b --factory-startup --python pipeline/wreck.py -- --input "$input" --output "out/$name/$tag.glb" --output "out/$name/$tag.blend" \
      --report "out/$name/$tag.json" --severity "$sev" --seed "$seed" --profile "$profile" $WRECK_ARGS > "out/$name/$tag.log" 2>&1
  fi
  if [ -f "out/$name/$tag.blend" ]; then echo "ok   $name $tag"; else echo "FAIL $name $tag: $(grep -E 'could not|Error|blew up|unsupported' "out/$name/$tag.log" | tail -1)"; fi
}
export -f one
grep -v '^#' "$1" | while read -r line; do for s in ${SEVS:-0 0.3 0.6 0.9}; do echo "$line|$s"; done; done > out/_matrix_jobs.txt
xargs -P "${JOBS:-6}" -a out/_matrix_jobs.txt -d '\n' -I{} bash -c 'one "$1"' _ {}
