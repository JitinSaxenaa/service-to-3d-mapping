import os, json, base64, io, requests
from PIL import Image

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5vl:7b"

steps = json.load(open("steps.json"))["steps"]
renders_dir = "renders"

def img_b64(path, max_dim=500):
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()

steps_text = "\n".join(f"Step {s['step_number']}: {s['title']}" for s in steps)

files = sorted(os.listdir(renders_dir))
file_list_text = "\n".join(f"- {f}" for f in files)
images_b64 = [img_b64(os.path.join(renders_dir, f)) for f in files]

prompt = f"""
You are looking at {len(files)} 3D renders of a microscope model, in this order:
{file_list_text}

Filenames encode which mesh(es) are highlighted in RED (00_overview.png has no
highlight - use it only for overall context). "cluster_XX.png" = a group of
identical small parts (screws/clips). "large_Mesh_N.png" = one large single part.

Repair steps to map:
{steps_text}

For every step, decide which highlighted render(s) show the part(s) involved.
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