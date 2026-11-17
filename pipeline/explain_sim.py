"""Render the recorded simulation: the sheet being struck, stage by stage, with the tools visible.

    blender -b --factory-startup --python pipeline/gpu_prefs.py --python pipeline/explain_sim.py -- --sim out/x/run.sim --out out/x/sim_frames

The .sim file comes from `wreck.py --record`.
"""
import argparse
import math
import os
import pickle
import sys

import bpy
import numpy as np
from mathutils import Vector

p = argparse.ArgumentParser()
p.add_argument("--sim", required=True)
p.add_argument("--out", required=True)
p.add_argument("--per-stage", type=int, default=6)
p.add_argument("--lead", type=int, default=20)
p.add_argument("--tail", type=int, default=30)
p.add_argument("--size", type=int, default=720)
p.add_argument("--samples", type=int, default=32)
p.add_argument("--only", type=int, nargs="*", help="render just these frame numbers (checking)")
a = p.parse_args(sys.argv[sys.argv.index("--") + 1:])
rec = pickle.load(open(os.path.abspath(a.sim), "rb"))
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def mat(name, rgb, rough=0.5, metallic=0.0, alpha=1.0, wire=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Alpha"].default_value = alpha
    if wire:
        w = m.node_tree.nodes.new("ShaderNodeWireframe")
        w.use_pixel_size = True
        w.inputs["Size"].default_value = 0.8
        mx = m.node_tree.nodes.new("ShaderNodeMix")
        mx.data_type = "RGBA"
        mx.inputs[6].default_value = (*rgb, 1)
        mx.inputs[7].default_value = (*wire, 1)
        m.node_tree.links.new(w.outputs[0], mx.inputs[0])
        m.node_tree.links.new(mx.outputs[2], b.inputs["Base Color"])
    return m


def obj(name, verts, faces, material):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(map(float, v)) for v in verts], [], faces)
    me.update()
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    o = bpy.data.objects.new(name, me)
    o.data.materials.append(material)
    scene.collection.objects.link(o)
    return o


rest = rec["rest"].astype(np.float64)
sheet = obj("sheet", rest, rec["faces"], mat("sheet", (0.50, 0.56, 0.62), 0.42, 0.6, wire=(0.26, 0.31, 0.37)))
tool_mat = mat("tool", (1.0, 0.35, 0.05), 0.35, 0.0, alpha=0.45)

# tools per hit, clipped to what is near the object so a 5-unit plate does not fill the frame
stages = rec["stages"]
hits = {}
for s in stages:
    if "tools" in s:
        objs = []
        for (tv, tf), l0 in zip(s["tools"], s["locs0"]):
            world = tv.astype(np.float64) + np.asarray(l0)
            keep = [f for f in tf if min(np.linalg.norm(world[i]) for i in f) < 1.2]
            o = obj("tool", tv, keep or tf, tool_mat)
            o.location = l0
            o.hide_render = True
            objs.append(o)
        hits[s["hit"]] = dict(objs=objs, locs0=[np.asarray(x) for x in s["locs0"]])

# timeline: lead (clean sheet), per-stage frames, tail (result)
frames = []  # (coords_a, coords_b, t, hit, locs_a, locs_b)
prev_co, prev_locs, prev_hit = rest, None, None
for s in stages:
    h = s["hit"]
    la = hits[h]["locs0"] if h != prev_hit else prev_locs
    lb = [np.asarray(x) for x in s["locs"]]
    for i in range(a.per_stage):
        frames.append((prev_co, s["co"].astype(np.float64), (i + 1) / a.per_stage, h, la, lb))
    prev_co, prev_locs, prev_hit = s["co"].astype(np.float64), lb, h
total = a.lead + len(frames) + a.tail

zmin = float(rest[:, 2].min())
bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 0, zmin))
bpy.context.object.data.materials.append(mat("floor", (0.19, 0.20, 0.21), 0.65))
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.42, 0.46, 0.52, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.55
scene.world = world
pivot = bpy.data.objects.new("pivot", None)
scene.collection.objects.link(pivot)
for name, ang, elev, power, size, col in (("key", 40, 48, 900, 1.2, (1, 0.95, 0.88)), ("rim", 200, 30, 600, 0.8, (0.8, 0.9, 1)),
                                           ("fill", -70, 15, 220, 2.5, (1, 1, 1))):
    ld = bpy.data.lights.new(name, "AREA")
    ld.energy = power * 0.32
    ld.size = size
    ld.color = col
    lo = bpy.data.objects.new(name, ld)
    az, el = math.radians(ang), math.radians(elev)
    lo.location = Vector((3.2 * math.cos(el) * math.cos(az), 3.2 * math.cos(el) * math.sin(az), 3.2 * math.sin(el)))
    lo.rotation_euler = (-lo.location).to_track_quat("-Z", "Y").to_euler()
    lo.parent = pivot  # lights turn with the camera so the struck side is always lit
    scene.collection.objects.link(lo)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.lens = 70
scene.collection.objects.link(cam)
el = math.radians(16)
cam.location = Vector((4.6 * math.cos(el), 0, 4.6 * math.sin(el)))
cam.rotation_euler = (-cam.location).to_track_quat("-Z", "Y").to_euler()
cam.parent = pivot
scene.camera = cam

scene.render.engine = "CYCLES"
scene.cycles.samples = a.samples
scene.cycles.use_denoising = True
scene.cycles.device = "GPU"
scene.render.resolution_x = scene.render.resolution_y = a.size
scene.render.image_settings.file_format = "PNG"
scene.view_settings.view_transform = "AgX"
os.makedirs(os.path.abspath(a.out), exist_ok=True)


def target_az(h):
    """Look at the struck side, a little off the tool's axis so the dent is seen in profile."""
    l0 = hits[h]["locs0"][0]
    return math.atan2(l0[1], l0[0]) + math.radians(38)


az = target_az(stages[0]["hit"]) - math.radians(50)
for f in range(total):
    k = f - a.lead
    for h in hits.values():
        for o in h["objs"]:
            o.hide_render = True
    if k < 0:
        coords, tgt = rest, target_az(stages[0]["hit"])
    elif k >= len(frames):
        coords, tgt = prev_co, az + math.radians(4.5)  # slow turn on the result
    else:
        ca, cb, t, h, la, lb = frames[k]
        coords = ca + (cb - ca) * t
        for o, x, y in zip(hits[h]["objs"], la, lb):
            o.location = x + (y - x) * t
            o.hide_render = False
        tgt = target_az(h)
    d = (tgt - az + math.pi) % (2 * math.pi) - math.pi
    az += d * (0.10 if 0 <= k < len(frames) else 1.0 if k >= len(frames) else 0.08)
    if a.only and f not in a.only:
        continue
    sheet.data.vertices.foreach_set("co", np.asarray(coords, dtype=np.float32).ravel())
    sheet.data.update()
    pivot.rotation_euler = (0, 0, az)
    scene.render.filepath = os.path.join(os.path.abspath(a.out), f"f_{f:04d}.png")
    bpy.ops.render.render(write_still=True)
print("explain_sim done", total, "frames")
