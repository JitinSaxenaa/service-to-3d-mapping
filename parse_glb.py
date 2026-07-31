"""
Step 1: Parse the chaotic GLB into structured mesh metadata.
Output: mesh_metadata.json - one entry per mesh with geometry stats
used later for fastener clustering and as VLM-agent context.
"""
import json
import trimesh
import numpy as np

GLB_PATH = "gearbox_service_unit.glb"
OUT_PATH = "mesh_metadata.json"

scene = trimesh.load(GLB_PATH, process=False)

records = []
for name, geom in scene.geometry.items():
    bbox = geom.bounds
    extent = (bbox[1] - bbox[0]).tolist()
    centroid = geom.centroid.tolist()
    records.append({
        "mesh_id": name,
        "raw_name_hint": name,   # unverified -- messy CAD export names, use as a weak hint only
        "vertex_count": int(len(geom.vertices)),
        "face_count": int(len(geom.faces)) if hasattr(geom, "faces") else None,
        "bbox_min": bbox[0].tolist(),
        "bbox_max": bbox[1].tolist(),
        "extent": extent,
        "max_extent": float(max(extent)),
        "centroid": centroid,
    })

# find which scene graph node(s) reference each mesh, for parent/transform info
node_by_mesh = {}
for node_name in scene.graph.nodes_geometry:
    transform, geom_name = scene.graph.get(node_name)
    node_by_mesh.setdefault(geom_name, []).append(node_name)

for r in records:
    r["node_names"] = node_by_mesh.get(r["mesh_id"], [])

# quick size-based clustering: group meshes with near-identical vertex_count
# and max_extent -> likely repeated hardware (screws, clips, nuts)
from collections import defaultdict
buckets = defaultdict(list)
for r in records:
    key = (r["vertex_count"], round(r["max_extent"], 1))
    buckets[key].append(r["mesh_id"])

fastener_clusters = {str(k): v for k, v in buckets.items() if len(v) >= 3}

with open(OUT_PATH, "w") as f:
    json.dump({
        "total_meshes": len(records),
        "meshes": records,
        "candidate_fastener_clusters": fastener_clusters,
    }, f, indent=2)

print(f"Wrote {len(records)} mesh records -> {OUT_PATH}")
print(f"Candidate fastener/hardware clusters (size>=3): {len(fastener_clusters)}")
for k, v in list(fastener_clusters.items())[:5]:
    print(" ", k, "->", v)
