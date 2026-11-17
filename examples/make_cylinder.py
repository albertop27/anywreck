"""Clean test input: a welded steel gas cylinder (1.25 m, foot ring, collar, valve) with UVs and two materials.

    blender -b --factory-startup --python make_cylinder.py -- out/cylinder_clean.glb
"""
import math
import os
import sys

import bmesh
import bpy

out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
SEG = 64


def lathe(bm, profile, mat, uv, u_scale=1.0, v0=0.0, v_scale=1.0):
    """Revolve (r, z) points round Z. UV: u round the body, v along the profile."""
    rings = []
    length = [0.0]
    for (r0, z0), (r1, z1) in zip(profile, profile[1:]):
        length.append(length[-1] + math.hypot(r1 - r0, z1 - z0))
    for r, z in profile:
        rings.append([bm.verts.new((r * math.cos(2 * math.pi * j / SEG), r * math.sin(2 * math.pi * j / SEG), z)) for j in range(SEG)])
    faces = []
    for i in range(len(rings) - 1):
        for j in range(SEG):
            j2 = (j + 1) % SEG
            f = bm.faces.new((rings[i][j], rings[i][j2], rings[i + 1][j2], rings[i + 1][j]))
            f.material_index = mat
            f.smooth = True
            us = (j / SEG, (j + 1) / SEG, (j + 1) / SEG, j / SEG)
            vs = (length[i], length[i], length[i + 1], length[i + 1])
            for loop, u, v in zip(f.loops, us, vs):
                loop[uv].uv = (u * u_scale, v0 + v * v_scale)
            faces.append(f)
    return faces


def arc(cx, cz, rx, rz, a0, a1, n):
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cz + rz * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


bm = bmesh.new()
uv = bm.loops.layers.uv.new("UVMap")
R, Z0, Z1 = 0.115, 0.13, 1.02  # wall radius, where the straight wall starts and ends
# body: bottom head, wall, shoulder, neck
body = [(0.0, 0.065)] + arc(0, Z0, R, 0.065, -90, 0, 10)[1:]
body += [(R, Z0 + (Z1 - Z0) * i / 28) for i in range(1, 29)]
body += arc(0, Z1, R, 0.10, 0, 72, 12)[1:]
body += [(0.030, 1.135), (0.030, 1.165), (0.0, 1.165)]
lathe(bm, body, 0, uv, v_scale=0.62)
# foot ring: an open skirt with real thickness
lathe(bm, [(R - 0.012, 0.11), (R - 0.004, 0.11), (R - 0.004, 0.0), (R - 0.016, 0.0), (R - 0.016, 0.085), (R - 0.012, 0.11)], 0, uv, v0=0.80, v_scale=0.5)
# collar round the valve
lathe(bm, [(0.060, 1.08), (0.072, 1.08), (0.076, 1.23), (0.066, 1.23), (0.060, 1.08)], 0, uv, v0=0.90, v_scale=0.3)
# weld beads at the two head seams
for z in (Z0, Z1):
    lathe(bm, [(R - 0.002 + 0.005 * math.cos(math.radians(a)), z + 0.007 * math.sin(math.radians(a))) for a in range(-150, 151, 50)], 0, uv, v0=0.75, v_scale=0.5)
# valve: stem, body, handwheel
lathe(bm, [(0.0, 1.16), (0.017, 1.16), (0.017, 1.20), (0.024, 1.205), (0.024, 1.235), (0.012, 1.24), (0.012, 1.262),
           (0.034, 1.264), (0.034, 1.276), (0.0, 1.276)], 1, uv, u_scale=0.3, v0=0.0, v_scale=0.5)
bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-6)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])

me = bpy.data.meshes.new("gas_cylinder")
bm.to_mesh(me)
bm.free()
ob = bpy.data.objects.new("gas_cylinder", me)
bpy.context.scene.collection.objects.link(ob)


def material(name, rgb, metallic, rough):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    return m


me.materials.append(material("cyl_paint", (0.50, 0.065, 0.035), 0.0, 0.42))
me.materials.append(material("cyl_brass", (0.62, 0.45, 0.18), 1.0, 0.35))
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB")
bpy.ops.wm.save_as_mainfile(filepath=os.path.splitext(os.path.abspath(out))[0] + ".blend")
print("wrote", out, len(me.polygons), "faces")
