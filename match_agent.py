import os, json, base64, io, requests
from PIL import Image
from collections import defaultdict

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5vl:7b"

steps = json.load(open("steps.json"))["steps"]
renders_dir = "renders"
meta = json.load(open("mesh_metadata.json"))

# Rebuild the exact same cluster order parse_glb.py used, so cluster_00.png
# lines up with the right entry in candidate_fastener_clusters.
cluster_items = list(meta["candidate_fastener_clusters"].items())
cluster_by_index = {i: mesh_ids for i, (key, mesh_ids) in enumerate(cluster_items)}

def img_b64(path, max_dim=500):
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()

steps_text = "\n".join(f"Step {s['step_number']}: {s['title']}" for s in steps)
files = sorted(os.listdir(renders_dir))

# Build a per-file "real candidate mesh_ids" annotation
def candidates_for(fname):
    if fname.startswith("cluster_"):
        idx = int(fname.replace("cluster_", "").replace(".png", ""))
        ids = cluster_by_index.get(idx, [])
        return f"actual mesh_ids highlighted red: {ids}"
    if fname.startswith("large_"):
        mid = fname.replace("large_", "").replace(".png", "")
        return f"actual mesh_id highlighted red: [{mid}]"
    return "no highlight, overview only"

file_list_text = "\n".join(f"- {f} ({candidates_for(f)})" for f in files)
images_b64 = [img_b64(os.path.join(renders_dir, f)) for f in files]

prompt = f"""
You are looking at {len(files)} 3D renders of a microscope model, in this order.
Each line tells you the filename AND the real mesh_id(s) highlighted red in that image.
You must ONLY use mesh_ids that appear in this list — never invent one.

{file_list_text}

"cluster_XX.png" = a group of near-identical small parts (screws/clips) — pick
the specific mesh_id(s) from that cluster's list that best fit the step, not
necessarily all of them. "large_Mesh_N.png" = one large single part.

Repair steps to map:
{steps_text}

For every step, decide which mesh_id(s) (from the real IDs listed above only)
are involved, and cite which render(s) you used as evidence.

Respond ONLY with valid JSON, no other text, in this exact shape:
{{"1": {{"mesh_ids": ["Mesh_x"], "source_renders": ["cluster_00.png"], "reasoning": "..."}}, "2": {{...}}}}
"""

resp = requests.post(OLLAMA_URL, json={
    "model": MODEL,
    "messages": [{"role": "user", "content": prompt, "images": images_b64}],
    "stream": False,
    "options": {"temperature": 0.1}
})

raw = resp.json()["message"]["content"]
print(raw)

cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
mapping = json.loads(cleaned)
json.dump(mapping, open("mapping.json", "w"), indent=2)
print("Saved mapping.json")