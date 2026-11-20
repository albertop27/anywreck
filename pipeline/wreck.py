"""AnyWreck: turn a clean mesh into a dented / crushed / bent version of itself.

    blender -b --factory-startup --python pipeline/wreck.py -- --input clean.glb --output broken.glb --severity 0.6 --seed 3

How it works
  1. The input meshes are copied into one closed, evenly meshed shell (voxel remesh): the "sheet".
  2. The sheet is simulated as stiff, nearly inextensible cloth and struck by collision objects:
     balls and bars (dents), a tilted plate against an anvil (crush), a bar over two supports (bend).
     Every few frames the deformed shape becomes the new rest shape, so the sheet yields like metal
     instead of springing back, and later hits land on earlier damage.
  3. Places near hard edges of the input (end caps, ribs, rims, seams) get stiffer: they are braced.
  4. The original meshes follow the sheet through a Surface Deform binding, so their topology, UVs and
     materials are kept; long edges are split first so they have the vertices to show a dent.
  5. A per-vertex `_damage` attribute (x = contact, y = crease, z = displacement) is written for wear shaders.
"""
import argparse
import json
import math
import os
import sys
import time

import bmesh
import bpy
import numpy as np
from mathutils import Vector, kdtree

T0 = time.time()
SELFC = os.environ.get("DMG_SELFC", "0") == "1"


def log(*a):
    print("[wreck]", *a, flush=True)


MATERIALS = {
    # tension, shear, bending, brace bending multiplier, depth scale
    "thin": dict(tension=60.0, shear=40.0, bending=12.0, brace=10.0, depth=1.15, hold=0.06),
    "sheet": dict(tension=80.0, shear=60.0, bending=150.0, brace=8.0, depth=1.0, hold=0.2),
    "heavy": dict(tension=100.0, shear=80.0, bending=400.0, brace=5.0, depth=0.6, hold=0.4),
}


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="wreck.py")
    p.add_argument("--input", required=True, help=".glb .gltf .obj .fbx .stl or .blend")
    p.add_argument("--output", required=True, action="append", help="repeatable; .glb .blend .obj .fbx")
    p.add_argument("--object", help="for .blend input: damage this object (and its children) only")
    p.add_argument("--severity", type=float, default=0.5, help="0..1")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--mode", default="auto", help="auto, or any of dent+crush+bend joined by +")
    p.add_argument("--size-mode", choices=["relative", "real"], default="relative",
                   help="relative: dents and rust patterns scale with the object (upstream). real: they keep their size in metres, "
                        "as tuned on an object whose middle side is --ref-size, so a car gets more, smaller dents than its own scale suggests")
    p.add_argument("--ref-size", type=float, default=0.35, help="metres; middle side of the object the damage was tuned on (20 l jerry can)")
    p.add_argument("--hits", help="JSON list of hits (file path or inline); overrides --mode")
    p.add_argument("--material", choices=sorted(MATERIALS), help="default: the profile's, else sheet")
    p.add_argument("--res", type=int, default=110, help="sheet resolution: voxels across the bbox diagonal")
    p.add_argument("--smooth", type=int, default=3, help="smoothing passes on the displacement (0 = raw simulation)")
    p.add_argument("--detail", type=int, default=80, help="output edges are split below diagonal/detail; 0 = keep topology")
    p.add_argument("--pressure", type=float, default=0.0, help="internal pressure (sealed vessel bulges when crushed)")
    p.add_argument("--tris", type=int, default=0, help="decimate the result to about this many triangles (0 = keep all)")
    p.add_argument("--no-floor", action="store_true", help="do not put the result back on its original lowest Z")
    p.add_argument("--report")
    p.add_argument("--profile", help="JSON (file or inline) describing what this object is and how it wears and breaks; see profiles/")
    p.add_argument("--wear", type=float, help="0..1 rust / paint wear amount; defaults to the severity")
    p.add_argument("--keep-proxy", action="store_true", help="leave the sheet in a .blend output (debug)")
    p.add_argument("--record", help="write the simulation (sheet per stage, tool positions) to this file for explainer renders")
    return p.parse_args(argv)


# ---------------------------------------------------------------- io

def load(path, obj_name):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if ext in (".glb", ".gltf"):
            bpy.ops.import_scene.gltf(filepath=path)
        elif ext == ".obj":
            bpy.ops.wm.obj_import(filepath=path)
        elif ext == ".fbx":
            bpy.ops.import_scene.fbx(filepath=path)
        elif ext == ".stl":
            bpy.ops.wm.stl_import(filepath=path)
        else:
            raise SystemExit(f"unsupported input type {ext}")
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if obj_name:
        root = scene.objects.get(obj_name)
        if root is None:
            raise SystemExit(f"no object named {obj_name!r}")
        keep = {root, *root.children_recursive}
        for o in list(scene.objects):
            if o not in keep:
                bpy.data.objects.remove(o)
        meshes = [o for o in keep if o.type == "MESH"]
    if not meshes:
        raise SystemExit("no mesh objects in input")
    return meshes


def save(paths, targets, whole_scene):
    for o in bpy.context.scene.objects:
        o.select_set(o in targets)
    bpy.context.view_layer.objects.active = targets[0]
    for path in paths:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        ext = os.path.splitext(path)[1].lower()
        if ext == ".blend":
            bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(path), copy=True)
        elif ext in (".glb", ".gltf"):
            bpy.ops.export_scene.gltf(filepath=path, export_format="GLB" if ext == ".glb" else "GLTF_SEPARATE",
                                      use_selection=not whole_scene, export_attributes=True)
        elif ext == ".obj":
            bpy.ops.wm.obj_export(filepath=path, export_selected_objects=not whole_scene)
        elif ext == ".fbx":
            bpy.ops.export_scene.fbx(filepath=path, use_selection=not whole_scene)
        else:
            raise SystemExit(f"unsupported output type {ext}")
        log("wrote", path)


# ---------------------------------------------------------------- mesh helpers

def co(me):
    a = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", a)
    return a.reshape(-1, 3).astype(np.float64)


def set_co(me, a):
    me.vertices.foreach_set("co", np.asarray(a, dtype=np.float32).ravel())
    me.update()


def vnormals(me):
    a = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertex_normals.foreach_get("vector", a)
    return a.reshape(-1, 3).astype(np.float64)


def edges_of(me):
    a = np.empty(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", a)
    return a.reshape(-1, 2)


def area_of(me):
    a = np.empty(len(me.polygons), dtype=np.float32)
    me.polygons.foreach_get("area", a)
    return float(a.sum())


def volume_of(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    v = bm.calc_volume(signed=False)
    bm.free()
    return float(v)


def curvature(me, E):
    """Mean normal change per unit length over each vertex's edges."""
    V, N = co(me), vnormals(me)
    k = np.linalg.norm(N[E[:, 0]] - N[E[:, 1]], axis=1) / np.maximum(np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1), 1e-9)
    s = np.zeros(len(V))
    c = np.zeros(len(V))
    for i in (0, 1):
        np.add.at(s, E[:, i], k)
        np.add.at(c, E[:, i], 1)
    return s / np.maximum(c, 1)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / max(np.linalg.norm(v), 1e-12)


def perp_frame(d):
    d = unit(d)
    h = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = unit(np.cross(d, h))
    return u, np.cross(d, u)


def new_obj(name, verts, faces):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


# ---------------------------------------------------------------- targets

def prepare_target(o, max_len_local):
    """Drop stale custom normals (keeping hard edges) and split long edges so dents have vertices."""
    if o.data.users > 1:
        o.data = o.data.copy()
    me = o.data
    if "custom_normal" in me.attributes:
        me.attributes.remove(me.attributes["custom_normal"])
        me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
        me.set_sharp_from_angle(angle=math.radians(40))
    if max_len_local <= 0:
        return
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    for _ in range(12):
        long = [e for e in bm.edges if e.calc_length() > max_len_local]
        if not long:
            break
        bmesh.ops.subdivide_edges(bm, edges=long, cuts=1)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.to_mesh(me)
    bm.free()
    me.update()


def joined_world_bmesh(targets):
    dg = bpy.context.evaluated_depsgraph_get()
    bm = bmesh.new()
    for o in targets:
        ev = o.evaluated_get(dg)
        me = bpy.data.meshes.new_from_object(ev)
        me.transform(o.matrix_world)
        bm.from_mesh(me)
        bpy.data.meshes.remove(me)
    return bm


def sharp_samples(bm, spacing):
    pts = []
    for e in bm.edges:
        hard = len(e.link_faces) != 2 or e.calc_face_angle(0.0) > math.radians(35)
        if not hard:
            continue
        a, b = e.verts[0].co, e.verts[1].co
        n = max(1, int((b - a).length / spacing))
        for i in range(n + 1):
            pts.append(a.lerp(b, i / n)[:])
    return np.array(pts, dtype=np.float64).reshape(-1, 3)


def drop_small_islands(me, frac=0.02):
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    seen = set()
    islands = []
    for v in bm.verts:
        if v.index in seen:
            continue
        stack, isl = [v], []
        seen.add(v.index)
        while stack:
            x = stack.pop()
            isl.append(x)
            for e in x.link_edges:
                y = e.other_vert(x)
                if y.index not in seen:
                    seen.add(y.index)
                    stack.append(y)
        islands.append(isl)
    big = max(len(i) for i in islands)
    # Hollow inputs give the shell an inside surface too. Nothing ever strikes it, so it would stay put and
    # hold the wall next to it: keep the outermost shell, and anything that is not inside its bounding box.
    boxes = []
    for isl in islands:
        xs = np.array([v.co[:] for v in isl])
        boxes.append((xs.min(0), xs.max(0)))
    outer = int(np.argmax([np.prod(hi - lo) for lo, hi in boxes]))
    olo, ohi = boxes[outer]
    kill = []
    for n_i, (isl, (lo, hi)) in enumerate(zip(islands, boxes)):
        inside = n_i != outer and bool((lo >= olo - 1e-6).all() and (hi <= ohi + 1e-6).all())
        if len(isl) < frac * big or inside:
            kill += isl
    if kill:
        bmesh.ops.delete(bm, geom=kill, context="VERTS")
    bm.to_mesh(me)
    bm.free()
    me.update()
    return len(islands), len(kill)


# ---------------------------------------------------------------- impactors (sim space)

def make_ball(r):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=4, radius=r)
    me = bpy.data.meshes.new("dmg_ball")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("dmg_ball", me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def make_bar(r, axis, length=5.0, segs=32, rings=40):
    axis = unit(axis)
    u, v = perp_frame(axis)
    verts, faces = [], []
    for i in range(rings + 1):
        c = axis * (length * (i / rings - 0.5))
        for j in range(segs):
            a = 2 * math.pi * j / segs
            verts.append(c + r * (math.cos(a) * u + math.sin(a) * v))
    for i in range(rings):
        for j in range(segs):
            a, b = i * segs + j, i * segs + (j + 1) % segs
            faces.append((a, b, b + segs, a + segs))
    ob = new_obj("dmg_bar", verts, faces)
    fix_normals(ob)
    return ob


def make_plate(normal, size=5.0, n=30):
    """Square grid through the origin whose front faces along `normal`."""
    normal = unit(normal)
    u, v = perp_frame(normal)
    verts, faces = [], []
    for i in range(n + 1):
        for j in range(n + 1):
            verts.append(u * (size * (i / n - 0.5)) + v * (size * (j / n - 0.5)))
    for i in range(n):
        for j in range(n):
            a = i * (n + 1) + j
            faces.append((a, a + 1, a + n + 2, a + n + 1))
    ob = new_obj("dmg_plate", verts, faces)
    me = ob.data
    if Vector(me.polygons[0].normal).dot(Vector(normal)) < 0:
        me.flip_normals()
    return ob


def fix_normals(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(ob.data)
    bm.free()


def collider(ob, cd):
    ob.modifiers.new("col", "COLLISION")
    ob.collision.thickness_outer = max(cd * 0.5, 0.001)
    ob.collision.thickness_inner = 0.2
    ob.collision.cloth_friction = 12.0
    ob.collision.damping = 0.5
    return ob


# ---------------------------------------------------------------- planning

def plan_hits(V, N, brace, sev, seed, mode, depth_scale, mix, size_ratio=1.0):
    """Hits in sim space. depth is a length along dir. size_ratio > 1 (real-size mode, object bigger than the reference):
    dents get smaller relative to the object and more numerous (by area, at most 4x). Crush and bend stay relative."""
    rng = np.random.default_rng(seed)
    lo, hi = V.min(0), V.max(0)
    ext = hi - lo
    ax = int(np.argmax(ext))
    axis = np.eye(3)[ax]
    lat = float(np.sort(ext)[1])  # middle extent: the "thickness" a side hit works against
    kinds = {"dent", "crush", "bend"} if mode == "auto" else set(mode.split("+"))
    auto = mode == "auto"
    s = float(np.clip(sev, 0, 1))
    hits = []

    def lateral_dir():
        u, v = perp_frame(axis)
        a = rng.uniform(0, 2 * math.pi)
        return math.cos(a) * u + math.sin(a) * v

    dents = []
    if "dent" in kinds and s > 0 and mix.get("dent", 1.0) > 0:
        n = max(1, int(round((1 + 3 * s) * mix.get("dent", 1.0) * min(max(size_ratio, 1.0) ** 2, 4.0))) + int(rng.integers(0, 2)))
        free = np.flatnonzero(brace < 0.3)
        if len(free) == 0:
            free = np.arange(len(V))
        for _ in range(n):
            i = int(rng.choice(free))
            d = unit(-N[i] + rng.normal(0, 0.22, 3))
            shape = "ball" if rng.random() < 0.7 else "bar"
            r = (rng.uniform(0.3, 1.1) * lat / 2 if shape == "ball" else rng.uniform(0.07, 0.16) * lat / 2) / size_ratio
            depth = rng.uniform(0.55, 1.0) * (0.05 + 0.27 * s) * lat * depth_scale / size_ratio
            h = dict(type="dent", shape=shape, vi=i, point=V[i].tolist(), dir=d.tolist(), radius=float(r), depth=float(depth))
            if shape == "bar":
                u, v = perp_frame(d)
                a = rng.uniform(0, math.pi)
                h["axis"] = (math.cos(a) * u + math.sin(a) * v).tolist()
            dents.append(h)
    big = []
    if "crush" in kinds and mix.get("crush", 1.0) > 0 and (not auto or s >= 0.45):
        t = s if not auto else (s - 0.45) / 0.55
        axial = rng.random() < mix.get("axial", 0.25)
        d = -axis if axial else lateral_dir()
        along = ext[ax] if axial else lat
        side = np.cross(d, axis) if not axial else perp_frame(d)[0]
        tilt = math.radians(rng.uniform(5, 16)) * rng.choice([-1, 1])
        # tilt the plate so one end takes more of the crush
        nrm = unit(math.cos(tilt) * (-d) + math.sin(tilt) * (axis if not axial else side))
        big.append(dict(type="crush", dir=d.tolist(), normal=nrm.tolist(),
                        depth=float((0.10 + 0.42 * t) * along * depth_scale * mix.get("crush", 1.0))))
    if "bend" in kinds and mix.get("bend", 1.0) > 0 and (not auto or s >= 0.7):
        t = s if not auto else (s - 0.7) / 0.3
        d = lateral_dir()
        big.append(dict(type="bend", dir=d.tolist(), at=float(rng.uniform(0.38, 0.62)),
                        radius=float(0.22 * lat), depth=float((0.15 + 0.45 * t) * lat * depth_scale * mix.get("bend", 1.0))))
    k = len(dents) // 2
    hits = dents[:k] + big + dents[k:]
    return hits


# ---------------------------------------------------------------- simulation

class Sim:
    def __init__(self, ob, voxel, mat, pressure):
        self.ob = ob
        self.voxel = voxel
        self.cd = max(0.35 * voxel, 0.0015)
        self.mat = mat
        self.pressure = pressure
        self.step = 0.35 * voxel
        self.stage_frames = 8
        self.frames = 0
        self.contact = np.zeros(len(ob.data.vertices))
        self.brace = np.zeros(len(ob.data.vertices))
        self.rec, self.cur_tools, self.hit_no = [], [], -1

    def cloth(self, nframes, pin, selfc):
        ob = self.ob
        vg = ob.vertex_groups.get("pin")
        if vg:
            ob.vertex_groups.remove(vg)
        # every vertex is held to the current shape a little (the wall resists), braced ones more
        hold = np.clip(self.mat["hold"] * (1.0 + 4.0 * self.brace), 0, 0.9)
        pin = hold if pin is None else np.maximum(pin, hold)
        if True:
            vg = ob.vertex_groups.new(name="pin")
            for w in np.unique(np.round(pin, 2)):
                if w > 0:
                    vg.add(np.flatnonzero(np.round(pin, 2) == w).tolist(), float(w), "REPLACE")
        m = ob.modifiers.new("cloth", "CLOTH")
        s, c, k = m.settings, m.collision_settings, self.mat
        s.quality = 12
        s.mass = 0.3
        s.air_damping = 6.0
        s.tension_stiffness = s.compression_stiffness = k["tension"]
        s.shear_stiffness = k["shear"]
        s.bending_stiffness = k["bending"]
        s.tension_damping = s.compression_damping = s.shear_damping = 25.0
        s.bending_damping = 2.0
        s.vertex_group_bending = "brace"
        s.bending_stiffness_max = k["bending"] * k["brace"]
        s.vertex_group_structural_stiffness = "brace"
        s.tension_stiffness_max = s.compression_stiffness_max = k["tension"] * 2
        s.effector_weights.gravity = 0.0
        if pin is not None:
            s.vertex_group_mass = "pin"
            s.pin_stiffness = 1.0
        if self.pressure > 0:
            s.use_pressure = True
            s.uniform_pressure_force = self.pressure
        c.use_collision = True
        c.collision_quality = 4
        c.distance_min = self.cd
        c.use_self_collision = selfc
        c.self_distance_min = self.cd
        c.self_friction = 5.0
        m.point_cache.frame_start = 1
        m.point_cache.frame_end = nframes
        return m

    def push(self, movers, d, travel, pin=None, selfc=False):
        """Move `movers` by `travel` along d, yielding the sheet every stage."""
        scene = bpy.context.scene
        n = max(1, int(math.ceil(travel / self.step)))
        d = unit(d)
        starts = [np.array(o.location) for o in movers]
        locs0 = [np.array(o.location) for o in self.cur_tools]
        self.hit_no += 1
        done = 0
        while done < n:
            k = min(self.stage_frames, n - done)
            for o, s0 in zip(movers, starts):
                o.animation_data_clear()
                o.location = s0 + d * (travel * done / n)
                o.keyframe_insert("location", frame=1)
                o.location = s0 + d * (travel * (done + k) / n)
                o.keyframe_insert("location", frame=k + 1)
            m = self.cloth(k + 2, pin, selfc)
            for f in range(1, k + 2):
                scene.frame_set(f)
            dg = bpy.context.evaluated_depsgraph_get()
            ev = self.ob.evaluated_get(dg)
            new = co(ev.data)
            self.ob.modifiers.remove(m)
            if not np.isfinite(new).all():
                raise RuntimeError("simulation blew up (non-finite coordinates)")
            set_co(self.ob.data, new)
            # for explainer renders: the sheet after this stage, where each tool is, and (first stage of a hit) the tools
            entry = dict(hit=self.hit_no, co=new.astype(np.float32), locs=[np.array(o.matrix_world.translation) for o in self.cur_tools])
            if done == 0:
                entry["locs0"] = locs0
                entry["tools"] = [(co(o.data).astype(np.float32), [tuple(p.vertices) for p in o.data.polygons]) for o in self.cur_tools]
            self.rec.append(entry)
            done += k
            self.frames += k
        scene.frame_set(1)
        for o in movers:
            o.animation_data_clear()

    def mark(self, dist, gain=1.0):
        self.contact = np.maximum(self.contact, gain * (1 - smoothstep(self.cd * 1.5, self.cd * 6, dist)))

    # each hit: place the tool just clear of the sheet, drive it in by depth, record what it touched
    def hit(self, h):
        V = co(self.ob.data)
        d = unit(h["dir"])
        clear = self.cd * 2.5
        junk = []
        selfc = False
        pin = None
        if h["type"] == "dent":
            # earlier hits move the surface: strike the planned vertex where it is now, or the nearest point
            p = V[h["vi"]] if "vi" in h else np.array(h["point"], dtype=np.float64)
            p = V[int(np.argmin(((V - p) ** 2).sum(1)))]
            r = float(h["radius"])
            rel = V - p
            along = rel @ d
            if h.get("shape", "ball") == "bar":
                a = unit(h["axis"])
                a = unit(a - d * (a @ d))
                side = rel - np.outer(along, d) - np.outer(rel @ a, a)
                tool = make_bar(r, a)
            else:
                side = rel - np.outer(along, d)
                tool = make_ball(r)
            perp2 = (side ** 2).sum(1)
            inside = perp2 < r * r
            t0 = float((along[inside] - np.sqrt(r * r - perp2[inside])).min()) - r - clear
            # centre sits r behind the surface it touches
            start = p + d * (t0 + r)
            tool.location = start
            # the rest of the object holds the struck area in place
            dist = np.linalg.norm(V - p, axis=1)
            r1 = 2.2 * r + 1.5 * h["depth"]
            pin = smoothstep(r1, 2.0 * r1, dist)
            movers = [tool]
            travel = clear + h["depth"]
            end = start + d * travel
            junk += [tool]
            for o in junk:
                collider(o, self.cd)
            self.cur_tools = list(junk)
            self.push(movers, d, travel, pin=pin)
            V2 = co(self.ob.data)
            if h.get("shape", "ball") == "bar":
                rel = V2 - end
                q = rel - np.outer(rel @ a, a)
                self.mark(np.abs(np.linalg.norm(q, axis=1) - r))
            else:
                self.mark(np.abs(np.linalg.norm(V2 - end, axis=1) - r))
        elif h["type"] == "crush":
            nrm = unit(h.get("normal", -d))  # plate front faces the object
            t = (V @ nrm) / (d @ nrm)
            # plate through point c = d*t touches the first vertex at min t
            t0 = float(t.min()) - clear / abs(d @ nrm)
            plate = make_plate(nrm)
            plate.location = d * t0
            far = float((V @ d).max()) + self.cd * 1.5
            anvil = make_plate(-d)
            anvil.location = d * far
            junk += [plate, anvil]
            for o in junk:
                collider(o, self.cd)
            self.cur_tools = list(junk)
            travel = clear + h["depth"]
            self.push([plate], d, travel, selfc=SELFC)
            V2 = co(self.ob.data)
            self.mark(np.abs((V2 - d * (t0 + travel)) @ nrm), 0.45)  # a flat plate rubs a whole face lightly; it does not strip it
            self.mark(np.abs(V2 @ d - far), 0.3)
        elif h["type"] == "bend":
            ext = V.max(0) - V.min(0)
            ax = int(np.argmax(ext))
            axis = np.eye(3)[ax]
            d = unit(d - axis * (d @ axis))
            a = np.cross(d, axis)  # bar runs across the long axis
            r = float(h["radius"])
            lo, hi = float(V[:, ax].min()), float(V[:, ax].max())
            pos = lo + (hi - lo) * float(h.get("at", 0.5))

            def place(station, dirn, radius):
                """Centre of a bar at `station` along the axis, just touching the sheet when moving along dirn."""
                rel = V - axis * station
                al = rel @ dirn
                s2 = (rel @ axis) ** 2
                ins = s2 < radius * radius
                if not ins.any():  # the bar station missed the mesh (open frames): use the nearest ring of vertices
                    ins = s2 <= s2.min() + 1e-9
                t = float((al[ins] - np.sqrt(radius * radius - s2[ins])).min()) - clear
                return axis * station + dirn * t

            tool = make_bar(r, a)
            start = place(pos, d, r)
            tool.location = start
            sup = []
            for f in (0.08, 0.92):
                sb = make_bar(r, a)
                sb.location = place(lo + (hi - lo) * f, -d, r)
                sup.append(sb)
            junk += [tool] + sup
            for o in junk:
                collider(o, self.cd)
            self.cur_tools = list(junk)
            travel = clear + h["depth"]
            self.push([tool], d, travel, selfc=SELFC)
            V2 = co(self.ob.data)
            for o, c in [(tool, start + d * travel)] + [(s, np.array(s.location)) for s in sup]:
                rel = V2 - c
                q = rel - np.outer(rel @ a, a)
                self.mark(np.abs(np.linalg.norm(q, axis=1) - r))
        else:
            raise SystemExit(f"unknown hit type {h['type']!r}")
        for o in junk:
            me = o.data
            bpy.data.objects.remove(o)
            bpy.data.meshes.remove(me)


def ridges(me, E):
    """How much each vertex of the mesh as it is now stands proud of its neighbours (0..1)."""
    V, N = co(me), vnormals(me)
    n = len(V)
    deg = np.zeros(n)
    s = np.zeros_like(V)
    for i, j in ((0, 1), (1, 0)):
        np.add.at(deg, E[:, i], 1)
        np.add.at(s, E[:, i], V[E[:, j]])
    deg = np.maximum(deg, 1)
    el = float(np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1).mean())
    cav = ((s / deg[:, None] - V) * N).sum(1) / el
    for _ in range(2):
        t = np.zeros(n)
        np.add.at(t, E[:, 0], cav[E[:, 1]])
        np.add.at(t, E[:, 1], cav[E[:, 0]])
        cav = 0.5 * cav + 0.5 * t / deg
    return smoothstep(0.03, 0.18, -cav)


def wear_mask(me, E, cur, crease, contact, seed, amount, W, ridge0=None, size_ratio=1.0):
    """Per-vertex (rust, grime, edge wear) from the damaged shape. W weights where rust starts, from the profile."""
    n = len(cur)
    N = vnormals(me)
    deg = np.zeros(n)
    for i in (0, 1):
        np.add.at(deg, E[:, i], 1)
    deg = np.maximum(deg, 1)

    def blur(x, passes=1):
        for _ in range(passes):
            s = np.zeros_like(x)
            np.add.at(s, E[:, 0], x[E[:, 1]])
            np.add.at(s, E[:, 1], x[E[:, 0]])
            x = 0.5 * x + 0.5 * s / (deg if x.ndim == 1 else deg[:, None])
        return x

    el = float(np.linalg.norm(cur[E[:, 0]] - cur[E[:, 1]], axis=1).mean())
    s = np.zeros_like(cur)
    np.add.at(s, E[:, 0], cur[E[:, 1]])
    np.add.at(s, E[:, 1], cur[E[:, 0]])
    cav = blur(((s / deg[:, None] - cur) * N).sum(1) / el, 2)  # > 0 in hollows, < 0 on ridges
    crevice = smoothstep(0.05, 0.2, cav)
    ridge = ridge0 if ridge0 is not None else smoothstep(0.03, 0.18, -cav)
    h = (cur[:, 2] - cur[:, 2].min()) / max(float(np.ptp(cur[:, 2])), 1e-9)
    ground = 1 - smoothstep(0.0, max(0.12 / size_ratio, 0.02), h)  # splash band: fixed metres in real-size mode
    pooling = smoothstep(0.6, 0.9, N[:, 2])  # upward faces hold water
    rng = np.random.default_rng(seed + 101)
    noise = np.zeros(n)
    for _ in range(7):
        k = unit(rng.normal(size=3)) * rng.uniform(2.0, 8.0) * min(size_ratio, 10.0)  # rust patches keep their size in metres
        noise += np.sin(cur @ k + rng.uniform(0, 2 * math.pi))
    noise = 0.5 + noise / (2 * 2.2)
    patch = smoothstep(0.5, 0.85, noise)
    terms = dict(patch=patch, crevice=crevice, ground=ground, pooling=pooling, crease=blur(crease, 1), scrape=blur(contact, 1))
    default = dict(patch=0.5, crevice=0.8, ground=0.7, pooling=0.6, crease=0.7, scrape=0.5)
    keep = np.ones(n)
    for name, m in terms.items():
        keep *= 1 - np.clip(float(W.get(name, default[name])) * m, 0, 1)
    k = float(W.get("rust", 1.0)) * amount
    rust = np.clip((1 - keep) * (0.45 + 0.75 * amount) * float(W.get("rust", 1.0)) + 0.2 * (noise - 0.5), 0, 1) * (amount > 0)
    edge = np.clip(ridge * float(W.get("edge_wear", 0.7)) * (0.4 + 1.2 * amount), 0, 1)
    grime = np.clip((0.6 * ground + 0.6 * crevice + 0.3 * (1 - patch)) * float(W.get("grime", 0.5)) * 1.5 * amount, 0, 1)
    return np.stack([rust, grime, edge], axis=1)


def kabsch(rest, cur, iters=4, sigma=0.03):
    """Rigid transform taking cur back onto rest, trusting the least-deformed vertices most."""
    w = np.ones(len(rest))
    R, t = np.eye(3), np.zeros(3)
    for _ in range(iters):
        cw = w / w.sum()
        ca, cb = cw @ cur, cw @ rest
        H = ((cur - ca) * cw[:, None]).T @ (rest - cb)
        U, _, Vt = np.linalg.svd(H)
        D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
        R = Vt.T @ D @ U.T
        t = cb - R @ ca
        res = np.linalg.norm(cur @ R.T + t - rest, axis=1)
        w = 1.0 / (1.0 + (res / sigma) ** 2)
    return cur @ R.T + t


# ---------------------------------------------------------------- main

def main():
    a = parse()
    profile = {}
    if a.profile:
        profile = json.loads(open(a.profile).read() if os.path.exists(a.profile) else a.profile)
    targets = load(a.input, a.object)
    whole_scene = not a.object
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 250
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    report = dict(input=a.input, severity=a.severity, seed=a.seed, mode=a.mode, material=a.material)

    bm = joined_world_bmesh(targets)
    W = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    lo, hi = W.min(0), W.max(0)
    centre, diag = (lo + hi) / 2, float(np.linalg.norm(hi - lo))
    k = 2.0 / diag  # world -> sim scale; the simulation always sees an object 2 units across
    voxel_w = diag / a.res
    report["size_m"] = [round(float(x), 4) for x in hi - lo]
    mid_m = float(np.sort(hi - lo)[1])
    size_ratio = float(np.clip(mid_m / a.ref_size, 0.5, 20.0)) if a.size_mode == "real" else 1.0
    report.update(size_mode=a.size_mode, size_ratio=round(size_ratio, 3))
    log(f"size mode {a.size_mode}: middle side {mid_m:.3f} m, ratio {size_ratio:.2f}")
    report["tris_in"] = int(sum(len(f.verts) - 2 for f in bm.faces))
    sharp = sharp_samples(bm, voxel_w)
    rest_area_targets = float(sum(f.calc_area() for f in bm.faces))

    if a.severity <= 0 and not a.hits and not a.wear:
        bm.free()
        save(a.output, targets, whole_scene)
        report.update(hits=[], note="severity 0: unchanged copy")
        finish(a, report)
        return

    # originals: tessellate so they have vertices to show a dent with
    for o in targets:
        sc = max(abs(s) for s in o.matrix_world.to_scale()) or 1.0
        prepare_target(o, (diag / a.detail) / sc if a.detail > 0 else 0)
    bpy.context.view_layer.update()

    # the sheet: one closed, evenly meshed shell of everything, and the originals bound to it at rest.
    # Surface Deform refuses a target with degenerate faces, which a voxel mesh sometimes has: try nearby cell sizes.
    # Walls thinner than a cell alias into ripples when voxelised (hollow crates, double-skinned panels). So the
    # shell is built from a copy with every surface pushed out along its normal by 3/4 of a cell, which thickens
    # thin walls by a cell and a half, and the result is snapped back onto the true surface afterwards.
    from mathutils.bvhtree import BVHTree
    surface = BVHTree.FromBMesh(bm)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)  # weld split corners so the inflated copy stays closed
    bm.normal_update()
    base_co = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    base_no = np.array([v.normal[:] for v in bm.verts], dtype=np.float64)
    me = bpy.data.meshes.new("dmg_join")
    bm.to_mesh(me)
    bm.free()
    tmp = bpy.data.objects.new("dmg_join", me)
    scene.collection.objects.link(tmp)
    rm = tmp.modifiers.new("remesh", "REMESH")
    rm.mode = "VOXEL"
    rm.adaptivity = 0.0
    voxel0, proxy = voxel_w, None
    for factor in (1.0, 0.94, 1.07, 0.88, 1.15, 0.82):
        voxel_w = voxel0 * factor
        set_co(me, base_co + base_no * (0.75 * voxel_w))
        rm.voxel_size = voxel_w
        pme = bpy.data.meshes.new_from_object(tmp.evaluated_get(bpy.context.evaluated_depsgraph_get()))
        if len(pme.vertices) < 200:
            raise SystemExit("could not build a closed shell from the input (open sheets and zero-thickness panels are not supported yet)")
        n_isl, n_kill = drop_small_islands(pme)
        pme.update()
        set_co(pme, co(pme) - vnormals(pme) * (0.75 * voxel_w))  # take the inflation back off, evenly
        # the simulation keeps the quads; the bind target is the same vertices triangulated
        quad_me = pme.copy()
        tb = bmesh.new()
        tb.from_mesh(pme)
        bmesh.ops.triangulate(tb, faces=tb.faces[:], quad_method="SHORT_EDGE")
        tb.to_mesh(pme)
        tb.free()
        proxy = bpy.data.objects.new("dmg_proxy", pme)
        scene.collection.objects.link(proxy)
        bound = True
        if bound:
            break
        for o in targets:
            for m in [m for m in o.modifiers if m.name.startswith("dmg_bind")]:
                o.modifiers.remove(m)
        bpy.data.objects.remove(proxy)
        bpy.data.meshes.remove(pme)
        bpy.data.meshes.remove(quad_me)
        proxy = None
    bpy.data.objects.remove(tmp)
    bpy.data.meshes.remove(me)
    if proxy is None:
        raise SystemExit("could not bind the input to the sheet at any nearby resolution; try another --res")
    Pw = co(pme)
    log(f"sheet: {len(Pw)} verts, {n_isl} islands ({n_kill} verts dropped), voxel {voxel_w * 1000:.1f} mm")

    # bracing from hard edges of the input
    brace = np.zeros(len(Pw))
    if len(sharp):
        kd = kdtree.KDTree(len(sharp))
        for i, p in enumerate(sharp):
            kd.insert(p, i)
        kd.balance()
        dist = np.array([kd.find(p)[2] for p in Pw])
        brace = 1.0 - smoothstep(1.0 * voxel_w, 4.0 * voxel_w, dist)
    report["braced_fraction"] = round(float((brace > 0.5).mean()), 3)

    # simulation copy in normalised space
    sme = quad_me
    set_co(sme, (Pw - centre) * k)
    sim_ob = bpy.data.objects.new("dmg_sim", sme)
    scene.collection.objects.link(sim_ob)
    vg = sim_ob.vertex_groups.new(name="brace")
    for w in np.unique(np.round(brace, 2)):
        if w > 0:
            vg.add(np.flatnonzero(np.round(brace, 2) == w).tolist(), float(w), "REPLACE")
    rest = co(sme)
    E = edges_of(sme)
    k0, area0, vol0 = curvature(sme, E), area_of(sme), volume_of(sme)
    ridge0 = ridges(sme, E)  # paint rubs off what stuck out on the clean object; measured now, before any ripples

    mat = MATERIALS[a.material or profile.get("material", "sheet")]
    report["material"] = a.material or profile.get("material", "sheet")
    if a.hits:
        src = open(a.hits).read() if os.path.exists(a.hits) else a.hits
        hits = json.loads(src)
        for h in hits:  # caller gives world units
            if "point" in h:
                h["point"] = ((np.array(h["point"]) - centre) * k).tolist()
            for key in ("depth", "radius"):
                if key in h:
                    h[key] = float(h[key]) * k
    else:
        hits = plan_hits(rest, vnormals(sme), brace, a.severity, a.seed, a.mode, mat["depth"] * float(profile.get("depth", 1.0)), profile.get("hits", {}), size_ratio)
    sim = Sim(sim_ob, voxel_w * k, mat, a.pressure)
    sim.brace = brace
    for i, h in enumerate(hits):
        t = time.time()
        sim.hit(h)
        log(f"hit {i + 1}/{len(hits)} {h['type']} depth {h['depth'] / k * 1000:.0f} mm  ({time.time() - t:.1f}s)")

    if a.record:
        import pickle
        with open(a.record, "wb") as f:
            pickle.dump(dict(rest=rest.astype(np.float32), faces=[tuple(p.vertices) for p in sme.polygons], stages=sim.rec,
                             hits=hits, scale=k, centre=centre), f)
        log("recorded", len(sim.rec), "stages to", a.record)
    cur = kabsch(rest, co(sme))
    # take out pleats finer than the sheet can honestly resolve; folds wider than ~3 cells survive
    dv = cur - rest
    deg = np.zeros(len(rest))
    for i in (0, 1):
        np.add.at(deg, E[:, i], 1)
    for _ in range(a.smooth):
        s = np.zeros_like(dv)
        np.add.at(s, E[:, 0], dv[E[:, 1]])
        np.add.at(s, E[:, 1], dv[E[:, 0]])
        dv = 0.5 * dv + 0.5 * s / np.maximum(deg, 1)[:, None]
    cur = rest + dv
    if not a.no_floor:
        cur[:, 2] += rest[:, 2].min() - cur[:, 2].min()
    set_co(sme, cur)
    k1 = curvature(sme, E)
    disp = np.linalg.norm(cur - rest, axis=1)
    crease = smoothstep(4.0, 30.0, k1 - k0)
    mask = np.stack([sim.contact * smoothstep(0.5 * sim.voxel, 3.0 * sim.voxel, disp), crease, np.clip(disp / 0.25, 0, 1)], axis=1)
    wear_amount = a.severity if a.wear is None else a.wear
    wear = wear_mask(sme, E, cur, crease, mask[:, 0], a.seed, wear_amount, profile.get("wear", {}), ridge0, size_ratio)
    mask = np.hstack([mask, wear])
    report["wear"] = dict(amount=round(float(wear_amount), 3), rust_cover=round(float((wear[:, 0] > 0.5).mean()), 3),
                          edge_wear_cover=round(float((wear[:, 2] > 0.5).mean()), 3))
    report.update(
        hits=[{**h, "depth_m": round(h["depth"] / k, 4)} for h in hits],
        sheet_verts=len(rest), sim_frames=sim.frames,
        area_ratio=round(area_of(sme) / area0, 4), volume_ratio=round(volume_of(sme) / vol0, 4),
        max_displacement_m=round(float(disp.max()) / k, 4),
    )

    # drive the originals: every vertex follows the sheet vertices nearest to it at rest, each of which carries
    # its own displacement and local rotation. (Blender's Surface Deform did this at first; it threw spikes on
    # boxes with inner faces and refused some voxel meshes.)
    cur_w = cur / k + centre
    set_co(pme, cur_w)
    dr, dc = Pw[E[:, 1]] - Pw[E[:, 0]], cur_w[E[:, 1]] - cur_w[E[:, 0]]
    outer = dr[:, :, None] * dc[:, None, :]
    H = np.zeros((len(Pw), 3, 3))
    np.add.at(H, E[:, 0], outer)
    np.add.at(H, E[:, 1], outer)
    U, _, Vt = np.linalg.svd(H)
    det = np.sign(np.linalg.det(np.einsum("nij,njk->nik", Vt.transpose(0, 2, 1), U.transpose(0, 2, 1))))
    Vt[:, 2, :] *= det[:, None]
    R = np.einsum("nij,njk->nik", Vt.transpose(0, 2, 1), U.transpose(0, 2, 1))  # rest -> deformed, per sheet vertex
    kd = kdtree.KDTree(len(Pw))
    for i, p in enumerate(Pw):
        kd.insert(p, i)
    kd.balance()
    KN = 6
    for o in targets:
        me = o.data
        mw = np.array(o.matrix_world)
        local = co(me)
        world = local @ mw[:3, :3].T + mw[:3, 3]
        idx = np.zeros((len(world), KN), dtype=np.int64)
        dist = np.zeros((len(world), KN))
        for vi, p in enumerate(world):
            near = kd.find_n(p, KN)
            idx[vi] = [i for _, i, _ in near]
            dist[vi] = [d for _, _, d in near]
        w = 1.0 / (dist ** 2 + (0.25 * voxel_w) ** 2)
        w /= w.sum(1, keepdims=True)
        off = world[:, None, :] - Pw[idx]                          # offset from each neighbour at rest
        rot = np.einsum("nkij,nkj->nki", R[idx], off)              # the same offset carried by that neighbour's rotation
        t = np.clip(1.0 - (dist - 2 * voxel_w) / (4 * voxel_w), 0, 1)[:, :, None]  # far from the sheet: translate only
        new_world = ((cur_w[idx] + off + t * (rot - off)) * w[:, :, None]).sum(1)
        inv = np.linalg.inv(mw)
        set_co(me, new_world @ inv[:3, :3].T + inv[:3, 3])
        vals = (mask[idx] * w[:, :, None]).sum(1).astype(np.float32)
        for name, cols in (("_damage", slice(0, 3)), ("_wear", slice(3, 6))):
            at = me.attributes.get(name) or me.attributes.new(name, "FLOAT_VECTOR", "POINT")
            at.data.foreach_set("vector", np.ascontiguousarray(vals[:, cols]).ravel())
        me.update()
    bpy.context.view_layer.update()
    tris = sum(len(p.vertices) - 2 for o in targets for p in o.data.polygons)
    report["tris_full"] = int(tris)
    if a.tris and tris > a.tris:
        # collapse back toward a game budget; UVs and the _damage attribute are interpolated
        for o in targets:
            dm = o.modifiers.new("dmg_decimate", "DECIMATE")
            dm.ratio = a.tris / tris
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        for o in targets:
            new = bpy.data.meshes.new_from_object(o.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
            o.modifiers.clear()
            new.name = o.data.name
            o.data = new
        bpy.context.view_layer.update()
    bm = joined_world_bmesh(targets)
    report["tris_out"] = int(sum(len(f.verts) - 2 for f in bm.faces))
    report["output_area_ratio"] = round(float(sum(f.calc_area() for f in bm.faces)) / rest_area_targets, 4)
    bm.free()


    bpy.data.objects.remove(sim_ob)
    if not a.keep_proxy:
        bpy.data.objects.remove(proxy)
    save(a.output, targets, whole_scene)
    finish(a, report)


def finish(a, report):
    report["seconds"] = round(time.time() - T0, 1)
    report["outputs"] = a.output
    if a.report:
        with open(a.report, "w") as f:
            json.dump(report, f, indent=1)
    log("REPORT " + json.dumps(report))


main()
