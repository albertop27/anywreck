#!/bin/bash
# Review videos from out/<name>/{clean,s30,s60,s90}.blend. Run from the repo root.
#   pipeline/grid.sh "oil_barrel gas_cylinder crate_yellow ..."
#   TILE=360 FRAMES=72 SAMPLES=24 GPU_JOBS=2 COLS=4
# -> out/videos/<name>_square.mp4   one mesh, 2x2: clean, 0.3 / 0.6, 0.9
# -> out/videos/grid.mp4            every mesh's square, COLS across (mesh type x severity)
# Every tile is a turntable shot from the same camera as its clean version.
export B="${BLENDER:-/c/Program Files/Blender Foundation/Blender 5.2/blender.exe}"
export TILE=${TILE:-360} FRAMES=${FRAMES:-72} SAMPLES=${SAMPLES:-24}
NAMES=${1:?list of names}
FONT='C\:/Windows/Fonts/arialbd.ttf'
tile() {
  name=$1; tag=$2
  n=$(ls "out/$name/tt_$tag" 2>/dev/null | grep -c png)
  [ "$n" = "$FRAMES" ] && return
  rm -rf "out/$name/tt_$tag"
  "$B" -b --factory-startup --python pipeline/gpu_prefs.py --python pipeline/turntable.py -- --input "out/$name/$tag.blend" \
    --ref "out/$name/clean.blend" --out "out/$name/tt_$tag" --wear --frames "$FRAMES" --size "$TILE" --samples "$SAMPLES" > "out/$name/tt_$tag.log" 2>&1
  echo "tile $name $tag $(ls "out/$name/tt_$tag" 2>/dev/null | grep -c png)"
}
export -f tile
for n in $NAMES; do for t in clean s30 s60 s90; do echo "$n $t"; done; done | xargs -P "${GPU_JOBS:-2}" -L1 bash -c 'tile $1 $2' _

mkdir -p out/videos
lab() { echo "drawtext=fontfile=$FONT:text='$1':x=10:y=h-26:fontsize=17:fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=5"; }
squares=()
for n in $NAMES; do
  title=$(echo "$n" | tr '_' ' ')
  ffmpeg -y -loglevel error -framerate 24 -i "out/$n/tt_clean/f_%04d.png" -framerate 24 -i "out/$n/tt_s30/f_%04d.png" \
    -framerate 24 -i "out/$n/tt_s60/f_%04d.png" -framerate 24 -i "out/$n/tt_s90/f_%04d.png" -filter_complex \
    "[0:v]$(lab "$title  clean")[a];[1:v]$(lab "0.3")[b];[2:v]$(lab "0.6")[c];[3:v]$(lab "0.9")[d];[a][b][c][d]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[g]" \
    -map "[g]" -c:v libx264 -pix_fmt yuv420p -crf 17 "out/videos/${n}_square.mp4" && squares+=("out/videos/${n}_square.mp4")
done

# all squares in one frame
COLS=${COLS:-4}; S=$((TILE*2)); inputs=(); layout=""; stack=""; i=0
for f in "${squares[@]}"; do
  inputs+=(-i "$f"); stack+="[$i:v]"; layout+="$(( (i%COLS)*S ))_$(( (i/COLS)*S ))|"; i=$((i+1))
done
if [ "$i" -gt 1 ]; then
  ffmpeg -y -loglevel error "${inputs[@]}" -filter_complex "${stack}xstack=inputs=$i:layout=${layout%|}:fill=black[g]" -map "[g]" \
    -c:v libx264 -pix_fmt yuv420p -crf 18 out/videos/grid_once.mp4
  ffmpeg -y -loglevel error -stream_loop 1 -i out/videos/grid_once.mp4 -c copy out/videos/grid.mp4 && rm out/videos/grid_once.mp4
  echo "wrote out/videos/grid.mp4 ($i meshes)"
fi
