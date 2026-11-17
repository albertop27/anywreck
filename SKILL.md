---
name: anywreck
description: Turn a clean 3D asset into a worn, dented, crushed and rusted version of itself, at a chosen severity, keeping its UVs and materials. Look at the object, write down what it is and how it gets hurt, then run a headless Blender simulation that hits it with real collision objects. Use it when someone wants damaged, aged or wrecked variants of a prop, or a set that looks used instead of factory-new.
---

# AnyWreck

AnyWreck takes a clean mesh and a severity from 0 to 1 and returns the same asset wrecked: dents, buckles, a crushed
side, rust where water sits, paint worn off the edges. Same seed, same wreck. The clean asset is never changed; the
result is a new file beside it.

Part of a family: AnyScene builds sets, AnyKit builds props, AnyMachine makes machines that move, AnyShot shoots
them. AnyWreck makes any of it look like it has had a life.

You are the person who knows how things break. The tool only knows how to hit a sheet of metal. What makes the
result believable is step 1: deciding what this object is, what it is made of, what happens to it and where it rusts.

## Requirements

| Tool | Notes |
|---|---|
| Blender 5.2 | `$BLENDER`. Background mode for everything. No GPU needed to wreck; a GPU only for review renders. |
| ffmpeg | `$FFMPEG`, for review videos. |
| bash | Git Bash on Windows. The scripts are run from the repo root. |

No keys, no network, no generation spend.

## Layout

```
pipeline/wreck.py        the tool: clean mesh in, wrecked mesh out
pipeline/matrix.sh       many meshes x several severities, in parallel
pipeline/turntable.py    review renders (shades rust from the masks)
pipeline/grid.sh         mesh-type x severity grid video
profiles/<object>.json   what an object is and how it wears and breaks (step 1)
examples/                clean test inputs and a jobs file
endpoint/server.py       local HTTP wrapper, kept for hosting later; not part of the workflow
out/<name>/              clean.blend, s30 / s60 / s90 .glb .blend .json, videos (not in git)
```

## The pipeline

Each step ends with a gate: something you look at before moving on. A run that exits cleanly proves nothing about
whether the wreck is believable.

### 1. Understand the object -> profile

Render or open the clean asset and look at it. Then write `profiles/<object>.json`. Answer, in sentences, in the
`understanding` list:

- What is it, and what is each part made of? Which parts are thin sheet, which are thick section or cast?
- What stays rigid when it is hit (rims, ribs, frames, corner castings, end caps), and what gives?
- How does it get hurt in its life? Struck from the side, dropped, stacked on, driven into, knocked over?
  What never happens to it?
- Where does paint wear first, and where does rust start? Where does water sit?

Then turn the answers into numbers:

| key | meaning |
|---|---|
| `material` | `thin` (foil, light drums), `sheet` (most fabricated steel), `heavy` (plate, castings, pressure vessels) |
| `hits.dent` | how many dents, relative (1 = default; 0 = none) |
| `hits.crush` | depth scale of a crush between a plate and the ground (0 = never crushed) |
| `hits.axial` | chance (0..1) that the crush comes along the long axis instead of across it |
| `hits.bend` | depth scale of folding over a bar (0 = never; most boxes and drums are 0) |
| `depth` | overall depth scale |
| `wear.rust` | overall rust |
| `wear.patch / crevice / ground / pooling / crease / scrape` | where rust starts: random patches, hollows, the band near the floor, upward faces that hold water, damage creases, struck areas |
| `wear.edge_wear` | paint rubbed off ridges and corners |
| `wear.grime` | dirt in hollows and near the floor |

Read two existing profiles first: `oil_barrel.json` (thin, crumples, never folds) and `genset.json` (mostly castings,
barely deforms, wreck is nearly all surface). If two objects are the same kind of thing, share a profile.

**Gate:** the `understanding` reads as true to someone who has handled the object, and every number follows from a
sentence in it.

### 2. Wreck

```
blender -b --factory-startup --python pipeline/wreck.py -- --input clean.glb --output out/x/s60.glb --output out/x/s60.blend \
    --report out/x/s60.json --severity 0.6 --seed 7 --profile profiles/x.json
```

Input `.glb .gltf .obj .fbx .stl .blend` (`--object NAME` for one object in a .blend). Output by extension.
Severity: about 0.3 is a few dents; from 0.45 a crush is added; from 0.7 a bend, if the profile allows one.
`--wear` sets rust and paint wear apart from the shape damage (default: the severity; `--severity 0 --wear 0.8`
ages without denting). `--hits` gives explicit strikes in world units instead of planned ones. `--tris N` decimates
to a budget. `--res` (default 110) trades speed for crease detail. Full list: `--help` text at the top of the file,
and README.md.

For several meshes and severities: write a jobs file (`name|input|profile|seed` per line) and run
`pipeline/matrix.sh jobs`.

**Gate:** read the report. `area_ratio` should be within about 3% of 1 (sheet metal does not stretch); if it is not,
the hits were too deep for the resolution: lower `depth` or raise `--res`. `volume_ratio` tells you how wrecked it
is. Then look at stills from four sides (`pipeline/turntable.py --still --wear`).

### 3. Look, and correct the profile

Look at the stills against the `understanding`. Typical corrections:

- It looks like cloth or a deflated bag: the object is stiffer than you said. Use `heavy`, or lower `depth`.
- A part that should be rigid has gone soft (a frame, a casting, a wheel): lower `crush`, set `bend` to 0.
  If it still deforms, that part needs to be excluded; see Limits.
- Rust is everywhere or nowhere: adjust the `wear` weights, not the severity.
- The same dent in the same place on every variant: change the seed.

**Gate:** you would believe a photograph of it.

### 4. Review video

```
blender -b --factory-startup --python pipeline/gpu_prefs.py --python pipeline/turntable.py -- --input out/x/s60.blend --ref out/x/clean.blend --out out/x/tt_s60 --wear --label "severity 0.6"
bash pipeline/grid.sh        # mesh type x severity grid from out/*/
```

Always deliver a video, not only stills.

## Two stages, on purpose

Stage 1 changes the shape. Stage 2 ages the surface. Stage 2 reads what stage 1 did, through two per-vertex
attributes on the result (glTF `_DAMAGE` and `_WEAR`):

- `_damage` = (contact: where a tool touched, crease: where the sheet folded, displacement)
- `_wear` = (rust, grime, edge wear)

Today stage 2 stops at those masks; `turntable.py --wear` shades from them for review. Not built yet, and meant to
plug in here without touching stage 1: baking the masks into the asset's colour / normal / ORM textures; decals;
growth by environment (moss, barnacles, salt), chosen in the profile.

## Limits (say these when you hand over a result)

- Closed, thin-walled, sheet-like objects work: drums, tanks, cylinders, crates, lockers, cabinets, containers.
- No tears, holes or parts breaking off. Nothing rips.
- Open single-sided sheets are refused (the tool needs a closed volume).
- Assemblies of rigid parts (racks with loose items, machines with wheels and pipes) are treated as one skin:
  small parts smear instead of moving rigidly.
- Walls can pass through each other at high severity (self-collision is off; it made the sheet pleat).
- The exported textures are still the clean ones; rust is in the masks.
- Rigs and armatures do not survive.

## Hosting later

The workflow comes first. `wreck.py` is already one process with file in, file and JSON report out, so it wraps
directly: `endpoint/server.py` is a working local example (POST a mesh, get the wrecked GLB). On fal it would be one
app with the profile step in front as a vision call. Do not deploy anything without the owner's say-so.
