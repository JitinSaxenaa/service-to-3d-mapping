"""
Step 3: Render the model so a VLM can actually "see" it.
- One full-assembly overview (a few angles)
- One highlighted render per fastener cluster (from step 2)
- One highlighted render per large/notable mesh (top-N by size)
Headless: matplotlib only, no GPU/display required.
"""
import json
import trimesh
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import os

GLB_PATH = "/mnt/user-data/uploads/microscope.glb"
META_PATH = "/home/claude/mesh_metadata.json"
OUT_DIR = "/home/claude/renders"
os.makedirs(OUT_DIR, exist_ok=True)

scene = trimesh.load(GLB_PATH, process=False)
meta = json.load(open(META_PATH))

# world-space transformed triangles per mesh (apply node transform)
def world_tris(mesh_id, max_tris=4000):
    node_names = None
    for r in meta["meshes"]:
        if r["mesh_id"] == mesh_id:
            node_names = r["node_names"]
            break
    geom = scene.geometry[mesh_id]
    tf = np.eye(4)
    if node_names:
        try:
            tf, _ = scene.graph.get(node_names[0])
        except Exception:
            pass
    verts = trimesh.transformations.transform_points(geom.vertices, tf)
    faces = geom.faces
    if len(faces) > max_tris:
        idx = np.random.choice(len(faces), max_tris, replace=False)
        faces = faces[idx]
    return verts[faces]  # (n_tris, 3, 3)

def render(highlight_ids, filename, title, angles=((25, -60),)):
    fig = plt.figure(figsize=(8, 8))
    for i, (elev, azim) in enumerate(angles):
        ax = fig.add_subplot(1, len(angles), i + 1, projection="3d")
        all_tris = []
        for r in meta["meshes"]:
            mid = r["mesh_id"]
            is_hi = mid in highlight_ids
            tris = world_tris(mid, max_tris=800 if not is_hi else 3000)
            color = (0.85, 0.15, 0.1, 0.95) if is_hi else (0.6, 0.6, 0.65, 0.12)
            pc = Poly3DCollection(tris, facecolor=color, edgecolor="none")
            ax.add_collection3d(pc)
            all_tris.append(tris.reshape(-1, 3))
        pts = np.concatenate(all_tris, axis=0)
        ax.set_xlim(pts[:, 0].min(), pts[:, 0].max())
        ax.set_ylim(pts[:, 1].min(), pts[:, 1].max())
        ax.set_zlim(pts[:, 2].min(), pts[:, 2].max())
        ax.set_box_aspect([1, 1, 1])
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, filename), dpi=110)
    plt.close(fig)

# 1. Full overview, 2 angles
render([], "00_overview.png", "Full assembly (no highlight)", angles=((25, -60), (25, 30)))
print("overview done")

# 2. One render per fastener cluster
clusters = meta["candidate_fastener_clusters"]
for i, (key, ids) in enumerate(clusters.items()):
    render(set(ids), f"cluster_{i:02d}.png", f"Fastener cluster {i} ({len(ids)} pieces): {ids[:4]}...")
    print("cluster", i, "done")

# 3. Top 20 largest meshes (likely major step-relevant parts)
by_size = sorted(meta["meshes"], key=lambda r: -r["max_extent"])[:20]
for r in by_size:
    render({r["mesh_id"]}, f"large_{r['mesh_id']}.png", f"{r['mesh_id']} (extent={r['max_extent']:.1f})")
print("large meshes done")

print("Total renders:", len(os.listdir(OUT_DIR)))
