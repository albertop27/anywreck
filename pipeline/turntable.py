"""Turntable frames of one asset (.blend from damage.py, or .glb).

    blender -b --factory-startup --python gpu_prefs.py --python turntable.py -- --input x.blend --ref clean.blend --out frames_dir [--frames 96] [--wear] [--still]

--ref frames the camera on the clean asset so every severity is shot identically.
--wear drives chipped paint / bare metal from the `_damage` attribute (render-side only; the asset's materials are untouched on disk).
"""
import argparse
import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
p = argparse.ArgumentParser()
p.add_argument("--input", required=True)
p.add_argument("--ref")
p.add_argument("--out", required=True)
p.add_argument("--frames", type=int, default=96)
p.add_argument("--size", type=int, default=720)
p.add_argument("--samples", type=int, default=48)
p.add_argument("--wear", action="store_true")
p.add_argument("--still", action="store_true", help="4 frames only, for checking")
p.add_argument("--label")
p.add_argument("--view", default="final", choices=["final", "damage", "wear"], help="damage / wear: show that mask in false colour")
p.add_argument("--range", type=int, nargs=2, help="render only frames [a, b) of the turn")
a = p.parse_args(argv)
a.input = os.path.abspath(a.input)
a.ref = os.path.abspath(a.ref) if a.ref else None
a.out = os.path.abspath(a.out)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def bring(path):
    before = set(bpy.data.objects)
    if path.lower().endswith(".blend"):
        with bpy.data.libraries.load(path) as (src, dst):
            dst.objects = list(src.objects)
        for o in dst.objects:
            if o and o.type == "MESH":
                scene.collection.objects.link(o)
    else:
        bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in set(bpy.data.objects) - before if o.type == "MESH" and o.name in scene.objects]


def bbox(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector([min(q[i] for q in pts) for i in range(3)])
    hi = Vector([max(q[i] for q in pts) for i in range(3)])
    return lo, hi


if a.ref:
    r = bring(a.ref)
    bpy.context.view_layer.update()
    lo, hi = bbox(r)
    for o in r:
        bpy.data.objects.remove(o)
objs = bring(a.input)
bpy.context.view_layer.update()
if not a.ref:
    lo, hi = bbox(objs)
centre = (lo + hi) / 2
radius = (hi - lo).length / 2

if a.wear:
    # Render-side wear: the asset's own materials are left alone on disk; this reads the `_damage` and `_wear`
    # vertex attributes the tool wrote. paint -> bare steel where struck, creased or rubbed -> rust on top -> grime.
    for m in {s.material for o in objs for s in o.material_slots if s.material}:
        nt = m.node_tree
        b = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if not b:
            continue
        L = nt.links.new

        def attr(name):
            n = nt.nodes.new("ShaderNodeAttribute")
            n.attribute_name = name
            s = nt.nodes.new("ShaderNodeSeparateXYZ")
            L(n.outputs["Vector"], s.inputs[0])
            return s.outputs

        def math_(op, x, y=None, clamp=False):
            n = nt.nodes.new("ShaderNodeMath")
            n.operation = op
            n.use_clamp = clamp
            for i, v in enumerate((x, y)):
                if v is None:
                    continue
                if isinstance(v, (int, float)):
                    n.inputs[i].default_value = v
                else:
                    L(v, n.inputs[i])
            return n.outputs[0]

        def step(x, lo, hi):
            n = nt.nodes.new("ShaderNodeMapRange")
            n.inputs["From Min"].default_value = lo
            n.inputs["From Max"].default_value = hi
            L(x, n.inputs["Value"])
            return n.outputs[0]

        def mix(fac, x, y, colour):
            n = nt.nodes.new("ShaderNodeMix")
            n.data_type = "RGBA" if colour else "FLOAT"
            ia, ib, o = (6, 7, 2) if colour else (2, 3, 0)
            L(fac, n.inputs[0])
            for i, v in ((ia, x), (ib, y)):
                if hasattr(v, "is_output"):
                    L(v, n.inputs[i])
                else:
                    n.inputs[i].default_value = v
            return n.outputs[o]

        def noise(scale, detail=5.0, stretch=None):
            n = nt.nodes.new("ShaderNodeTexNoise")
            n.inputs["Scale"].default_value = scale
            n.inputs["Detail"].default_value = detail
            co_ = nt.nodes.new("ShaderNodeTexCoord")
            if stretch:
                mp = nt.nodes.new("ShaderNodeMapping")
                mp.inputs["Scale"].default_value = stretch
                L(co_.outputs["Object"], mp.inputs["Vector"])
                L(mp.outputs[0], n.inputs["Vector"])
            else:
                L(co_.outputs["Object"], n.inputs["Vector"])
            return n.outputs[0]

        def current(name):
            inp = b.inputs[name]
            if inp.is_linked:
                return inp.links[0].from_socket
            v = inp.default_value
            return tuple(v) if hasattr(v, "__len__") else float(v)

        dmg, wear = attr("_damage"), attr("_wear")
        fine, blotch, streak = noise(140.0, 8.0), noise(16.0, 6.0), noise(22.0, 4.0, (1.0, 1.0, 0.05))
        # bare steel: contact scrapes, creases crack the paint, ridges rub through
        bare_src = math_("MAXIMUM", math_("MAXIMUM", math_("MULTIPLY", dmg[0], 0.62), math_("MULTIPLY", dmg[1], 0.85)), wear[2])
        bare = step(math_("ADD", bare_src, fine), 0.95, 1.10)
        # rust: the mask, broken up by blotches and pulled into streaks running down
        r = math_("ADD", wear[0], math_("MULTIPLY", math_("SUBTRACT", blotch, 0.5), 0.9))
        r = math_("ADD", r, math_("MULTIPLY", math_("SUBTRACT", streak, 0.5), 0.6))
        r = math_("ADD", r, math_("MULTIPLY", math_("SUBTRACT", fine, 0.5), 0.5))
        rust = step(r, 0.56, 0.60)
        rust_col = mix(step(blotch, 0.3, 0.7), (0.022, 0.010, 0.006, 1), (0.13, 0.04, 0.012, 1), True)
        rust_col = mix(step(fine, 0.55, 0.8), rust_col, (0.26, 0.085, 0.02, 1), True)
        # thin rust stain round the solid rust
        stain = math_("MULTIPLY", step(r, 0.38, 0.56), 0.5)
        col = mix(bare, current("Base Color"), (0.33, 0.31, 0.29, 1), True)
        col = mix(stain, col, (0.12, 0.05, 0.02, 1), True)
        col = mix(rust, col, rust_col, True)
        grime = math_("MULTIPLY", wear[1], math_("ADD", blotch, 0.2), clamp=True)
        col = mix(math_("MULTIPLY", grime, 0.6), col, (0.03, 0.028, 0.025, 1), True)
        met = mix(rust, mix(bare, current("Metallic"), 0.9, False), 0.0, False)
        rough = mix(rust, mix(bare, current("Roughness"), 0.36, False), 0.92, False)
        rough = mix(math_("MULTIPLY", grime, 0.5), rough, 0.8, False)
        L(col, b.inputs["Base Color"])
        L(met, b.inputs["Metallic"])
        L(rough, b.inputs["Roughness"])
        bump = nt.nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.6
        bump.inputs["Distance"].default_value = 0.002
        L(math_("MULTIPLY", rust, fine), bump.inputs["Height"])
        L(bump.outputs[0], b.inputs["Normal"])

if a.view in ("damage", "wear"):
    # false colour: show the mask the tool wrote instead of the asset's material
    vm = bpy.data.materials.new("mask_view")
    vm.use_nodes = True
    nt = vm.node_tree
    b = nt.nodes["Principled BSDF"]
    at = nt.nodes.new("ShaderNodeAttribute")
    at.attribute_name = "_" + a.view
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(at.outputs["Vector"], sep.inputs[0])
    cols = {"damage": ((1.0, 0.25, 0.02), (1.0, 0.85, 0.05), (0.05, 0.35, 1.0)),      # contact, crease, displacement
            "wear": ((0.85, 0.22, 0.03), (0.02, 0.02, 0.02), (0.9, 0.9, 0.95))}[a.view]  # rust, grime, edge wear
    prev = None
    base = (0.16, 0.17, 0.19, 1)
    for i in (2, 1, 0) if a.view == "damage" else (1, 0, 2):
        mx = nt.nodes.new("ShaderNodeMix")
        mx.data_type = "RGBA"
        nt.links.new(sep.outputs[i], mx.inputs[0])
        if prev is None:
            mx.inputs[6].default_value = base
        else:
            nt.links.new(prev, mx.inputs[6])
        mx.inputs[7].default_value = (*cols[i], 1)
        prev = mx.outputs[2]
    nt.links.new(prev, b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.6
    for o in objs:
        for s in o.material_slots:
            s.material = vm

# floor
bpy.ops.mesh.primitive_plane_add(size=radius * 40, location=(centre.x, centre.y, lo.z))
floor = bpy.context.object
fm = bpy.data.materials.new("floor")
fm.use_nodes = True
fb = fm.node_tree.nodes["Principled BSDF"]
fb.inputs["Base Color"].default_value = (0.19, 0.20, 0.21, 1)
fb.inputs["Roughness"].default_value = 0.65
floor.data.materials.append(fm)

# lights
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.42, 0.46, 0.52, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.55
scene.world = world
pivot = bpy.data.objects.new("pivot", None)
pivot.location = centre
scene.collection.objects.link(pivot)
for name, ang, elev, power, size, col in (("key", 40, 48, 900, 1.2, (1, 0.95, 0.88)), ("rim", 200, 30, 600, 0.8, (0.8, 0.9, 1)),
                                           ("fill", -70, 15, 220, 2.5, (1, 1, 1))):
    ld = bpy.data.lights.new(name, "AREA")
    ld.energy = power * radius * radius * 0.32
    ld.size = size * radius
    ld.color = col
    lo_ = bpy.data.objects.new(name, ld)
    d = radius * 3.2
    az, el = math.radians(ang), math.radians(elev)
    lo_.location = centre + Vector((d * math.cos(el) * math.cos(az), d * math.cos(el) * math.sin(az), d * math.sin(el)))
    lo_.rotation_euler = (centre - lo_.location).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(lo_)

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.lens = 70
scene.collection.objects.link(cam)
dist = radius * 4.6
el = math.radians(14)
cam.location = Vector((dist * math.cos(el), 0, dist * math.sin(el)))
cam.rotation_euler = (-cam.location).to_track_quat("-Z", "Y").to_euler()
cam.parent = pivot
scene.camera = cam

scene.render.engine = "CYCLES"
scene.cycles.samples = a.samples
scene.cycles.use_denoising = True
scene.cycles.device = "GPU"
scene.render.use_persistent_data = True
scene.render.resolution_x = scene.render.resolution_y = a.size
scene.render.image_settings.file_format = "PNG"
scene.view_settings.view_transform = "AgX"
if a.label:
    scene.render.use_stamp = True
    for k in ("date", "time", "render_time", "frame", "frame_range", "memory", "hostname", "camera", "lens", "scene", "marker", "filename"):
        if hasattr(scene.render, "use_stamp_" + k):
            setattr(scene.render, "use_stamp_" + k, False)
    scene.render.use_stamp_note = True
    scene.render.stamp_note_text = a.label
    scene.render.stamp_font_size = 22
    scene.render.stamp_background = (0, 0, 0, 0.45)

os.makedirs(a.out, exist_ok=True)
n = a.frames
todo = range(0, n, n // 4) if a.still else range(*a.range) if a.range else range(n)
for f in todo:
    pivot.rotation_euler = (0, 0, 2 * math.pi * f / n + math.radians(25))
    scene.render.filepath = os.path.join(a.out, f"f_{f:04d}.png")
    bpy.ops.render.render(write_still=True)
print("turntable done", a.out)
