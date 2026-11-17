"""Cut the explainer + results video from footage already rendered under out/. Plain Python (Blender's bundled one works).

    python pipeline/make_video.py                 -> out/videos/anywreck_explainer.mp4

Footage it expects: out/videos/barrel_{clean,s30,s60,s90,square}.mp4 (720 px turntables), out/explain/{sim,turn_*}
(pipeline/explain.sh), out/videos/<name>_square.mp4 (pipeline/grid.sh).
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
W, H, FPS = 1920, 1080, 24
BG, FG, DIM, ACC = "0x101215", "0xf2f4f6", "0x97a1ab", "0xff6a1a"
FONT = {"b": "out/video_tmp/segoeuib.ttf", "r": "out/video_tmp/segoeui.ttf", "l": "out/video_tmp/segoeuil.ttf"}  # copied from C:/Windows/Fonts (a drive colon cannot go in a filter)
TMP = "out/video_tmp"
os.makedirs(TMP, exist_ok=True)
for _f in ("segoeuib.ttf", "segoeui.ttf", "segoeuil.ttf"):
    if not os.path.exists(f"{TMP}/{_f}"):
        import shutil
        shutil.copy(f"C:/Windows/Fonts/{_f}", f"{TMP}/{_f}")
_n = [0]


def text(s, x, y, size=40, font="r", color=FG, t=0.0, until=None):
    """One drawtext. The string goes through a file so no escaping is needed."""
    _n[0] += 1
    path = f"{TMP}/t{_n[0]:03d}.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)
    alpha = f"if(lt(t,{t}),0,min(1,(t-{t})/0.35))"
    if until is not None:
        alpha = f"if(gt(t,{until}),0,{alpha})"
    return (f"drawtext=fontfile={FONT[font]}:textfile={path}:x={x}:y={y}:fontsize={size}:fontcolor={color}:"
            f"alpha='{alpha}':line_spacing=10:expansion=none")


def segment(name, dur, inputs, vis_filter, vis_xy, texts):
    """inputs: ffmpeg input args; vis_filter: graph that ends in [vis]; texts: list of drawtext strings."""
    out = f"{TMP}/{name}.mp4"
    graph = f"color=c={BG}:s={W}x{H}:r={FPS}:d={dur}[bg];"
    if vis_filter:
        graph += vis_filter + f";[bg][vis]overlay={vis_xy[0]}:{vis_xy[1]}:shortest=0:eof_action=repeat[c0]"
    else:
        graph += "[bg]null[c0]"
    chain = ",".join(texts + [f"fade=t=in:st=0:d=0.3,fade=t=out:st={dur - 0.3}:d=0.3"])
    graph += f";[c0]{chain}[out]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[out]", "-t", str(dur),
           "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(name, "FAILED\n", r.stderr[-1500:])
        sys.exit(1)
    print("segment", name, dur)
    return out


def head(step, title):
    return [text(step, 110, 150, 30, "b", ACC), text(title, 110, 196, 64, "b")]


def body(lines, y0=330, size=36, gap=64, t0=0.5, dt=0.9, color=FG):
    return [text(s, 110, y0 + i * gap, size, "r", color, t0 + i * dt) for i, s in enumerate(lines)]


VX, VY = 1090, 180  # 720 square on the right
segs = []
v = "out/videos"
rep = json.load(open("out/oil_barrel/s60.json"))

# 0. title: too perfect -> believable
segs.append(segment("s0_title", 5.0, ["-i", f"{v}/barrel_clean.mp4", "-i", f"{v}/barrel_s90.mp4"],
                    "[0:v][1:v]xfade=transition=fade:duration=1.4:offset=1.6[vis]", (VX, VY), [
                        text("AnyWreck", 110, 300, 132, "b"),
                        text("The most real things are the imperfect ones.", 110, 480, 44, "r", FG, 0.6),
                        text("clean mesh in, wrecked mesh out", 110, 560, 34, "r", DIM, 1.6),
                        text("clean", VX + 16, VY + 720 - 52, 30, "b", FG, 0.0, 2.2),
                        text("severity 0.9", VX + 16, VY + 720 - 52, 30, "b", ACC, 2.6)]))

# 1. understand
prof = json.load(open("profiles/oil_barrel.json"))
segs.append(segment("s1_understand", 8.0, ["-stream_loop", "1", "-i", f"{v}/barrel_clean.mp4"], "[0:v]null[vis]", (VX, VY),
                    head("STEP 1", "Understand the object") + body([
                        "Before anything is hit, the agent writes down",
                        "what this thing is and how it gets hurt:",
                    ], t0=0.4, dt=0.5) + body([
                        "thin rolled sheet: the wall dents and buckles",
                        "hoops and rims are the stiff parts, they stay round",
                        "struck side-on, squashed, never folded in half",
                        "rust: the puddle on the lid, the bottom rim, inside dents",
                        "paint rubs off the hoops first",
                    ], y0=500, size=32, gap=58, t0=1.6, dt=0.9, color=DIM) + [
                        text("profiles/oil_barrel.json", 110, 830, 26, "r", ACC, 6.0)]))

# 2. the simulation
n_sim = len([f for f in os.listdir("out/explain/sim") if f.endswith(".png")])
d2 = round(n_sim / FPS + 1.2, 2)
segs.append(segment("s2_sim", d2, ["-framerate", str(FPS), "-i", "out/explain/sim/f_%04d.png"], "[0:v]null[vis]", (VX, VY),
                    head("STEP 2", "Hit a sheet that cannot stretch") + body([
                        f"The asset becomes one closed sheet of {rep['sheet_verts']:,} points.",
                        "Balls, bars and plates strike it: real collisions,",
                        "not a dent painted into the surface.",
                        "Every 8 frames the bent shape becomes the rest shape,",
                        "so it yields like metal and stays bent.",
                    ], t0=0.5, dt=1.0) + [
                        text(f"surface area  {100 * (rep['area_ratio'] - 1):+.1f} %", 110, 720, 40, "b", ACC, 5.6),
                        text(f"volume  {100 * (rep['volume_ratio'] - 1):+.0f} %", 560, 720, 40, "b", ACC, 6.0),
                        text("It cannot sink like clay. It has to buckle and crease.", 110, 790, 30, "r", DIM, 6.4)]))

# 3. back onto the asset, and the masks
Q = len([f for f in os.listdir("out/explain/turn_shape") if f.endswith(".png")])
nf = len([f for f in os.listdir("out/explain/turn_final") if f.endswith(".png")])
q = Q / FPS
ins = []
for nm, start in (("turn_shape", 0), ("turn_damage", Q), ("turn_wear", 2 * Q), ("turn_final", 3 * Q)):
    ins += ["-framerate", str(FPS), "-start_number", str(start), "-i", f"out/explain/{nm}/f_%04d.png"]
d3 = round((3 * Q + nf) / FPS, 2)
segs.append(segment("s3_masks", d3, ins, "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[vis]", (VX, VY),
                    head("STEP 3", "Back onto the real asset") + [
                        text("The original mesh follows the sheet.\nSame UVs, same materials, same names.", 110, 340, 36, "r", FG, 0.3, q),
                        text("The tool also records where it was hit:\norange = struck   yellow = creased   blue = moved", 110, 340, 36, "r", FG, q, 2 * q),
                        text("and where age goes, from the profile:\nrust   grime   paint worn off the ridges", 110, 340, 36, "r", FG, 2 * q, 3 * q),
                        text("Rust and bare steel are shaded from those masks.\nRust sits where the damage and the water are.", 110, 340, 36, "r", FG, 3 * q),
                        text("shape", VX + 16, VY + 668, 30, "b", FG, 0.0, q),
                        text("damage mask", VX + 16, VY + 668, 30, "b", ACC, q, 2 * q),
                        text("wear mask", VX + 16, VY + 668, 30, "b", ACC, 2 * q, 3 * q),
                        text("result", VX + 16, VY + 668, 30, "b", FG, 3 * q)]))

# 4. one mesh, any severity
segs.append(segment("s4_severity", 7.0, ["-stream_loop", "1", "-i", f"{v}/barrel_square.mp4"], "[0:v]scale=960:960[vis]", (900, 60),
                    head("ONE MESH", "Any degree of wreck") + body([
                        "severity 0 to 1, one number",
                        "0.3   a few dents",
                        "0.6   dents and a squash",
                        "0.9   wrecked",
                        "same seed, same wreck",
                    ], t0=0.5, dt=0.7)))

# 5. any mesh: squares two at a time
names = [n for n in (sys.argv[1:] or ["lockers", "fuel_tank", "crate_orange", "module", "tool_cart", "genset", "gas_cylinder", "crate_yellow"])
         if os.path.exists(f"{v}/{n}_square.mp4")]
for i in range(0, len(names) - 1, 2):
    a, b = names[i], names[i + 1]
    segs.append(segment(f"s5_{i}", 4.5, ["-stream_loop", "1", "-i", f"{v}/{a}_square.mp4", "-stream_loop", "1", "-i", f"{v}/{b}_square.mp4"],
                        "[0:v][1:v]hstack=inputs=2[vis]", (240, 300), [
                            text("ANY MESH", 240, 90, 30, "b", ACC), text("Existing props, untouched originals", 240, 136, 64, "b"),
                            text("each square: clean, 0.3 / 0.6, 0.9", 240, 236, 30, "r", DIM)]))

# 6. end card
segs.append(segment("s6_end", 5.0, [], None, None, [
    text("AnyWreck", 110, 300, 120, "b"),
    text("a skill: look at the object, write its profile, wreck it, check it", 110, 470, 40, "r", FG, 0.5),
    text("works today: closed sheet-metal things. Drums, tanks, lockers, crates, containers", 110, 560, 32, "r", DIM, 1.2),
    text("next: rust baked into the textures, tears and holes, decals and growth", 110, 616, 32, "r", DIM, 1.8),
    text("part of the Any family: AnyScene  AnyKit  AnyMachine  AnyShot", 110, 760, 30, "r", ACC, 2.6)]))

lst = f"{TMP}/concat.txt"
with open(lst, "w") as f:
    for s in segs:
        f.write(f"file '{os.path.abspath(s).replace(os.sep, '/')}'\n")
out = "out/videos/anywreck_explainer.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out], check=True)
print("wrote", out)
