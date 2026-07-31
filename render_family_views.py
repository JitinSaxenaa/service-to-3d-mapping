"""
Render one highlighted view per canonical part family (pooled across ALL
geometry clusters it appears in, since e.g. socket cap screws got split
across 3 raw clusters). Confident members = green, suspect/duplicate-flagged
members = red, so it's visually obvious which ones need a judgment call.
"""
import trimesh
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import re, os, json
from collections import defaultdict

GLB_PATH = "/mnt/user-data/uploads/gearbox_service_unit.glb"
OUT_DIR = "/mnt/user-data/outputs/renders"
os.makedirs(OUT_DIR, exist_ok=True)

scene = trimesh.load(GLB_PATH, process=False)

FAMILY_TOKENS = {
    "GBX-HXB-122": ["hexbolt", "sechskant", "bolt_m6"],
    "GBX-DP-130":  ["dowel", "pin_loc"],
    "GBX-SCS-121": ["capscrw", "zylschraube", "skt_schr"],
    "GBX-BRG-123": ["roller_brg", "brg_cyl"],
    "GBX-GH-103":  ["helix_gr", "schraegverz", "hel_gear"],
    "GBX-OS-124":  ["oilseal", "simmerring", "wellendicht"],
    "GBX-NB-129":  ["nadellager", "needle_brg"],
}
SUSPECT_PATTERNS = [r"_old", r"donotuse", r"\(2\)", r"_copy", r"_final_final", r"^object\.", r"^solid_", r"^mesh_[0-9a-f]{6}", r"^body_x\d"]

def family_of(name):
    n = name.lower()
    for pn, tokens in FAMILY_TOKENS.items():
        if any(t in n for t in tokens):
            return pn
    return None

def is_suspect(name):
    n = name.lower()
    return any(re.search(p, n) for p in SUSPECT_PATTERNS)

by_family = defaultdict(list)
for name in scene.geometry.keys():
    fam = family_of(name)
    if fam:
        by_family[fam].append(name)

# pre-sample a capped number of vertices per mesh once, reused for every render
MAX_PTS_BG = 250     # background (greyed-out) meshes
MAX_PTS_HL = 1200    # highlighted meshes -- denser so shape reads clearly
sampled = {}
for name, geom in scene.geometry.items():
    v = geom.vertices
    if len(v) > MAX_PTS_HL:
        idx = np.random.choice(len(v), MAX_PTS_HL, replace=False)
        v = v[idx]
    sampled[name] = v

all_pts_global = np.vstack([g.vertices for g in scene.geometry.values()])

def plot_scene(highlight_confident, highlight_suspect, title, out_path, elev=25, azim=45):
    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(111, projection="3d")
    for name in scene.geometry.keys():
        v = sampled[name]
        if name in highlight_confident:
            color, size, alpha = "#2ecc71", 3, 0.9
        elif name in highlight_suspect:
            color, size, alpha = "#e74c3c", 3, 0.9
        else:
            v = v[:MAX_PTS_BG] if len(v) > MAX_PTS_BG else v
            color, size, alpha = "#95a5a6", 0.5, 0.15
        ax.scatter(v[:,0], v[:,1], v[:,2], c=color, s=size, alpha=alpha, linewidths=0)
    pts = all_pts_global
    ax.set_xlim(pts[:,0].min(), pts[:,0].max())
    ax.set_ylim(pts[:,1].min(), pts[:,1].max())
    ax.set_zlim(pts[:,2].min(), pts[:,2].max())
    ax.set_box_aspect([1,1,1])
    ax.view_init(elev=elev, azim=azim)
    ax.set_title(title, fontsize=10)
    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    plt.close(fig)
    print("wrote", out_path)

# overview, no highlight
plot_scene(set(), set(), "Full assembly - overview", f"{OUT_DIR}/00_overview.png")
plot_scene(set(), set(), "Full assembly - overview (alt angle)", f"{OUT_DIR}/00_overview_b.png", azim=135)

for fam, members in by_family.items():
    confident = {m for m in members if not is_suspect(m)}
    suspect = {m for m in members if is_suspect(m)}
    fname = f"{OUT_DIR}/family_{fam}.png"
    plot_scene(confident, suspect, f"{fam} - green=confident red=suspect/duplicate", fname)
