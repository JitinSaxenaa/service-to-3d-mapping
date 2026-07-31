"""
render_family_views.py (v2 — generic family list)

Same rendering internals as before (point-cloud style, since this GLB is too
dense for Poly3DCollection — see handoff gotchas). The only thing that
changed is WHERE the family list comes from:

    v1: hardcoded FAMILY_TOKENS = {"GBX-HXB-122": ["hexbolt", "sechskant", ...], ...}
    v2: matched_clusters.json, produced generically by match_meshes_to_parts.py

Color coding (now 3-way, up from 2-way):
    green  = confident member of this family (matcher score >= threshold,
             not suspect-named)
    red    = matched to this family BUT suspect-named (_old, donotuse, (2),
             _copy, etc.) -- probably a stale/duplicate mesh, needs a call
    yellow = AMBIGUOUS -- this mesh's top-2 candidate parts were too close
             to call from text alone, and this family is one of its top
             candidates. Rendering it in every family it's ambiguous
             between is exactly the visual disambiguation stage 4 (the VLM)
             is meant to resolve.

Unmatched meshes (no usable text signal at all -- placeholder names like
Object.086, solid_52, or untranslatable foreign-language names) simply
don't appear in any family view and stay grey/background, same as before.

Usage:
    python render_family_views.py \
        --glb gearbox_service_unit.glb \
        --matches matched_clusters.json \
        --out-dir renders
"""

import argparse
import json
import os
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import trimesh


def load_family_data(matches_path):
    """Build {family_pn: {"confident": set(mesh_ids), "suspect": set(mesh_ids)}}
    and {mesh_id: [candidate_pns]} for ambiguous meshes, straight from the
    generic matcher's output -- no hardcoded token lists.
    """
    with open(matches_path, encoding="utf-8") as f:
        data = json.load(f)

    clusters = data.get("clusters", {})
    ambiguous = data.get("ambiguous_meshes", [])
    suspect_ids = {s["mesh_id"] for s in data.get("suspect_meshes", [])}

    by_family = defaultdict(lambda: {"confident": set(), "suspect": set(), "ambiguous": set()})

    for pn, cluster in clusters.items():
        for mesh_id in cluster["mesh_ids"]:
            if mesh_id in suspect_ids:
                by_family[pn]["suspect"].add(mesh_id)
            else:
                by_family[pn]["confident"].add(mesh_id)

    # Ambiguous meshes get added to every family they were a live candidate for
    for entry in ambiguous:
        mesh_id = entry["mesh_id"]
        for cand in entry["top_candidates"]:
            by_family[cand["part_no"]]["ambiguous"].add(mesh_id)

    return by_family


def render(glb_path, matches_path, out_dir, max_pts_bg=250, max_pts_hl=1200):
    os.makedirs(out_dir, exist_ok=True)
    scene = trimesh.load(glb_path, process=False)
    by_family = load_family_data(matches_path)

    sampled = {}
    for name, geom in scene.geometry.items():
        v = geom.vertices
        if len(v) > max_pts_hl:
            idx = np.random.choice(len(v), max_pts_hl, replace=False)
            v = v[idx]
        sampled[name] = v

    all_pts_global = np.vstack([g.vertices for g in scene.geometry.values()])

    def plot_scene(confident, suspect, ambiguous, title, out_path, elev=25, azim=45):
        fig = plt.figure(figsize=(8, 8))
        ax = fig.add_subplot(111, projection="3d")
        for name in scene.geometry.keys():
            v = sampled[name]
            if name in confident:
                color, size, alpha = "#2ecc71", 3, 0.9
            elif name in suspect:
                color, size, alpha = "#e74c3c", 3, 0.9
            elif name in ambiguous:
                color, size, alpha = "#f1c40f", 3, 0.9
            else:
                v = v[:max_pts_bg] if len(v) > max_pts_bg else v
                color, size, alpha = "#95a5a6", 0.5, 0.15
            ax.scatter(v[:, 0], v[:, 1], v[:, 2], c=color, s=size, alpha=alpha, linewidths=0)
        pts = all_pts_global
        ax.set_xlim(pts[:, 0].min(), pts[:, 0].max())
        ax.set_ylim(pts[:, 1].min(), pts[:, 1].max())
        ax.set_zlim(pts[:, 2].min(), pts[:, 2].max())
        ax.set_box_aspect([1, 1, 1])
        ax.view_init(elev=elev, azim=azim)
        ax.set_title(title, fontsize=10)
        ax.set_axis_off()
        plt.tight_layout()
        plt.savefig(out_path, dpi=110)
        plt.close(fig)
        print("wrote", out_path)

    plot_scene(set(), set(), set(), "Full assembly - overview", f"{out_dir}/00_overview.png")
    plot_scene(set(), set(), set(), "Full assembly - overview (alt angle)",
               f"{out_dir}/00_overview_b.png", azim=135)

    for fam, groups in by_family.items():
        fname = f"{out_dir}/family_{fam}.png"
        title = f"{fam} - green=confident red=suspect yellow=ambiguous"
        plot_scene(groups["confident"], groups["suspect"], groups["ambiguous"], title, fname)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glb", default="gearbox_service_unit.glb")
    ap.add_argument("--matches", default="matched_clusters.json")
    ap.add_argument("--out-dir", default="renders")
    args = ap.parse_args()
    render(args.glb, args.matches, args.out_dir)


if __name__ == "__main__":
    main()
