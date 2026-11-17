"""Clean test input: a 200 litre steel oil drum (0.59 m x 0.88 m): rolled chimes, two rolling hoops, recessed lid, two bungs.

    blender -b --factory-startup --python make_barrel.py -- out/barrel_clean.glb
"""
import math
import os
import sys

import bmesh
import bpy

out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)


def lathe(bm, profile, mat, uv, seg=72, centre=(0.0, 0.0), u_scale=1.0, v0=0.0, v_scale=1.0):
    """Revolve (r, z) points round a vertical axis at `centre`. UV: u round, v along the profile."""
    length = [0.0]
    for (r0, z0), (r1, z1) in zip(profile, profile[1:]):
        length.append(length[-1] + math.hypot(r1 - r0, z1 - z0))
    rings = [[bm.verts.new((centre[0] + r * math.cos(2 * math.pi * j / seg), centre[1] + r * math.sin(2 * math.pi * j / seg), z))
              for j in range(seg)] for r, z in profile]
    for i in range(len(rings) - 1):
        for j in range(seg):
            j2 = (j + 1) % seg
            f = bm.faces.new((rings[i][j], rings[i][j2], rings[i + 1][j2], rings[i + 1][j]))
            f.material_index = mat
            f.smooth = True
            us = (j / seg, (j + 1) / seg, (j + 1) / seg, j / seg)
            vs = (length[i], length[i], length[i + 1], length[i + 1])
            for loop, u, v in zip(f.loops, us, vs):
                loop[uv].uv = (u * u_scale, v0 + v * v_scale)


bm = bmesh.new()
uv = bm.loops.layers.uv.new("UVMap")
R, H = 0.286, 0.88


def hoop(z, out_=0.011, half=0.024, n=6):
    return [(R + out_ * math.sin(math.pi * i / n), z - half + 2 * half * i / n) for i in range(n + 1)]


prof = [(0.0, 0.020), (R - 0.016, 0.020), (R - 0.014, 0.004), (R - 0.008, 0.0), (R + 0.006, 0.0), (R + 0.008, 0.008),
        (R + 0.006, 0.020), (R, 0.026)]
prof += hoop(0.30) + hoop(0.58)
prof += [(R, H - 0.026), (R + 0.006, H - 0.020), (R + 0.008, H - 0.008), (R + 0.006, H), (R - 0.008, H), (R - 0.014, H - 0.004),
         (R - 0.016, H - 0.022), (0.0, H - 0.022)]
lathe(bm, prof, 0, uv, v_scale=0.55)
# bungs on the lid: a 2 inch and a 3/4 inch
for (cx, cy), r in (((0.17, 0.0), 0.034), ((-0.17, 0.0), 0.018)):
    z = H - 0.022
    lathe(bm, [(r + 0.008, z - 0.002), (r + 0.008, z + 0.006), (r, z + 0.008), (r, z + 0.014), (r * 0.55, z + 0.014),
               (r * 0.55, z + 0.008), (0.0, z + 0.008)], 1, uv, seg=24, centre=(cx, cy), u_scale=0.2, v0=0.9, v_scale=0.5)
bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-6)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
me = bpy.data.meshes.new("oil_barrel")
bm.to_mesh(me)
bm.free()
ob = bpy.data.objects.new("oil_barrel", me)
bpy.context.scene.collection.objects.link(ob)


def material(name, rgb, metallic, rough):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    return m


me.materials.append(material("barrel_paint", (0.025, 0.11, 0.30), 0.0, 0.40))
me.materials.append(material("barrel_bung", (0.45, 0.45, 0.44), 1.0, 0.38))
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB")
bpy.ops.wm.save_as_mainfile(filepath=os.path.splitext(os.path.abspath(out))[0] + ".blend")
print("wrote", out, len(me.polygons), "faces")
