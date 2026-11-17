#!/bin/bash
# Explainer footage for one wrecked asset. Run from the repo root.
#   pipeline/explain.sh <name> <tag> <sim file>      e.g. pipeline/explain.sh oil_barrel s60 out/explain/barrel_s60.sim
# -> out/explain/sim/         the sheet being struck (from wreck.py --record)
#    out/explain/turn_*/      one continuous turn of the result in four looks: shape only, damage mask, wear mask, final
B="${BLENDER:-/c/Program Files/Blender Foundation/Blender 5.2/blender.exe}"
name=$1; tag=$2; sim=$3; N=${TURN:-160}; Q=$((N/4))
tt() { "$B" -b --factory-startup --python pipeline/gpu_prefs.py --python pipeline/turntable.py -- --input "out/$name/$tag.blend" \
        --ref "out/$name/clean.blend" --frames "$N" --size 720 --samples 40 "$@" 2>&1 | grep -aiE "Error|Traceback|turntable done"; }
"$B" -b --factory-startup --python pipeline/gpu_prefs.py --python pipeline/explain_sim.py -- --sim "$sim" --out out/explain/sim 2>&1 | grep -aiE "Error|Traceback|explain_sim done"
tt --out out/explain/turn_shape  --range 0 $Q
tt --out out/explain/turn_damage --range $Q $((2*Q)) --view damage
tt --out out/explain/turn_wear   --range $((2*Q)) $((3*Q)) --view wear
tt --out out/explain/turn_final  --range $((3*Q)) $((N+Q)) --wear
"$B" -b --factory-startup --python pipeline/gpu_prefs.py --python pipeline/turntable.py -- --input "out/$name/clean.blend" \
        --frames "$N" --size 720 --samples 40 --out out/explain/turn_clean --range 0 $Q 2>&1 | grep -aiE "Error|Traceback|turntable done"
