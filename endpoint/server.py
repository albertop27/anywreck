"""Thin local HTTP wrapper round damage.py. Listens on 127.0.0.1 only; nothing here is public.

    python server.py            (or: blender -b --python server.py)
    curl -X POST --data-binary @tank.glb "http://127.0.0.1:8765/damage?severity=0.6&seed=3&name=tank.glb" -o tank_s60.glb

POST /damage
  body            the mesh file (glb, gltf, obj, fbx, stl, blend), raw bytes
                  or JSON {"input_path": "D:/.../tank.glb", ...params} to use a file already on this machine
  query / JSON    name      file name of the upload (its extension picks the importer)   default input.glb
                  severity  0..1                                                         default 0.5
                  seed      integer, same seed = same damage                             default 0
                  mode      auto | dent | crush | bend | dent+crush ...                  default auto
                  material  thin | sheet | heavy                                         default sheet
                  format    glb | blend | obj | fbx                                      default glb
                  object    for .blend input, the object to damage
                  hits      JSON list of explicit hits, world units (see README)
                  profile   JSON object: what the object is and how it wears and breaks (see profiles/)
                  wear      0..1 rust and paint wear; defaults to severity
                  res, detail, pressure
  response        the damaged file; the report (hits, area and volume ratios, triangle counts) is in the
                  X-Damage-Report header as JSON. Add report=1 to get the JSON alone with output_path.
GET /health       {"ok": true}
"""
import json
import os
import subprocess
import sys
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.environ.get("BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
WORK = os.environ.get("DAMAGE_WORK", os.path.join(tempfile.gettempdir(), "anymachine_damage"))
PORT = int(os.environ.get("DAMAGE_PORT", "8765"))
PASS = ("severity", "seed", "mode", "material", "object", "res", "detail", "pressure", "tris", "smooth", "wear")
TYPES = {"glb": "model/gltf-binary", "blend": "application/x-blender", "obj": "text/plain", "fbx": "application/octet-stream"}


def run(params, body):
    job = os.path.join(WORK, uuid.uuid4().hex[:12])
    os.makedirs(job)
    if params.get("input_path"):
        src = params["input_path"]
        if not os.path.isfile(src):
            return 400, {"error": f"no such file: {src}"}, None
    else:
        if not body:
            return 400, {"error": "send the mesh as the request body, or input_path in JSON"}, None
        src = os.path.join(job, os.path.basename(params.get("name", "input.glb")))
        with open(src, "wb") as f:
            f.write(body)
    fmt = params.get("format", "glb")
    if fmt not in TYPES:
        return 400, {"error": f"format must be one of {sorted(TYPES)}"}, None
    out = os.path.join(job, f"damaged.{fmt}")
    rep = os.path.join(job, "report.json")
    cmd = [BLENDER, "-b", "--factory-startup", "--python", os.path.join(HERE, "..", "pipeline", "wreck.py"), "--",
           "--input", src, "--output", out, "--report", rep]
    for k in PASS:
        if params.get(k) not in (None, ""):
            cmd += [f"--{k}", str(params[k])]
    if params.get("profile"):
        pp = os.path.join(job, "profile.json")
        with open(pp, "w") as f:
            f.write(params["profile"] if isinstance(params["profile"], str) else json.dumps(params["profile"]))
        cmd += ["--profile", pp]
    if params.get("hits"):
        hp = os.path.join(job, "hits.json")
        with open(hp, "w") as f:
            f.write(params["hits"] if isinstance(params["hits"], str) else json.dumps(params["hits"]))
        cmd += ["--hits", hp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.isfile(out) or not os.path.isfile(rep):
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-8:])
        return 500, {"error": "damage failed", "log": tail}, None
    report = json.load(open(rep))
    report["output_path"] = out
    return 200, report, out


class H(BaseHTTPRequestHandler):
    def send_json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            return self.send_json(200, {"ok": True, "blender": os.path.isfile(BLENDER)})
        self.send_json(404, {"error": "POST /damage"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/damage":
            return self.send_json(404, {"error": "POST /damage"})
        params = dict(parse_qsl(u.query))
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        if (self.headers.get("Content-Type") or "").startswith("application/json"):
            try:
                params.update(json.loads(body or b"{}"))
            except ValueError:
                return self.send_json(400, {"error": "body is not valid JSON"})
            body = b""
        code, report, out = run(params, body)
        if code != 200 or str(params.get("report", "")) == "1":
            return self.send_json(code, report)
        data = open(out, "rb").read()
        slim = {k: v for k, v in report.items() if k != "hits"}
        self.send_response(200)
        self.send_header("Content-Type", TYPES[os.path.splitext(out)[1][1:]])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Damage-Report", json.dumps(slim))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *a):
        sys.stderr.write("[damage-server] " + fmt % a + "\n")


if __name__ == "__main__":
    os.makedirs(WORK, exist_ok=True)
    print(f"damage server on http://127.0.0.1:{PORT}  (work dir {WORK})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
