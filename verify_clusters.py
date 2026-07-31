"""
Stage 2.5: Cross-check each geometry cluster against canonical_parts.json's
reconciled quantities, and flag members whose names suggest they're not a
genuine distinct part (leftover duplicates, generic/unlabeled CAD junk).

This runs AFTER parse_glb.py, BEFORE match_agent.py -- its job is to hand the
VLM pre-sorted, qty-checked candidate groups instead of raw geometry buckets,
so the vision call only has to resolve genuine ambiguity, not do triage.
"""
import json
import re

# Paste your actual clusters here (from mesh_metadata.json -> candidate_fastener_clusters)
clusters = {
    "(148, 0.6)": ['pin_loc.005_rev04__g', 'sechskant34_v3__g', 'bolt_m6_09__g', 'body_x75__g', 'sechskant.034__g', 'sechskant28 (2)__g', 'hexbolt7__g', 'skt_schr.035_rev04__g', 'hexbolt.020_v3__g', 'bolt_m6.022_copy__g', 'sechskant29_imp__g', 'skt_schr_32_neu__g', 'hexbolt32_copy__g', 'skt_schr2__g', 'bolt_m6_31_final__g', 'skt_schr35_x__g', 'bolt_m6_21_neu__g', 'bolt_m6_38_rev04__g', 'hexbolt24_neu__g'],
    "(116, 1.2)": ['capscrw.037_rev04__g', 'dowel.003__g', 'Object.417__g', 'Object.589__g', 'dowel_22_x__g', 'mesh_7784d5__g', 'dowel_16_final__g'],
    "(21722, 2.4)": ['roller_brg.035_final__g', 'brg_cyl.030 (2)__g', 'roller_brg_30_rev04__g', 'brg_cyl14__g'],
    "(116, 1.5)": ['capscrw_37__g', 'zylschraube_29_neu__g', 'solid_33__g', 'mesh_b4502f__g', 'capscrw21__g', 'zylschraube8_final__g'],
    "(15566, 3.0)": ['helix_gr_02_rev04__g', 'helix_gr13_copy__g', 'schraegverz.008 (2)__g', 'helix_gr_08_x__g', 'mesh_e73f3a__g', 'hel_gear_18_old_DONOTUSE__g'],
    "(792, 2.4)": ['oilseal.038_old_DONOTUSE__g', 'simmerring.023 (2)__g', 'Object.662__g', 'wellendichtring1__g'],
    "(116, 2.2)": ['nadellager_02_x__g', 'needle_brg7_imp__g', 'needle_brg.014_final__g', 'needle_brg_36__g', 'nadellager.034__g', 'nadellager_39_x__g'],
}

# name-token family -> canonical part number (English + German tokens, both seen in this file)
FAMILY_TOKENS = {
    "GBX-HXB-122": ["hexbolt", "sechskant", "bolt_m6"],       # cover hex bolt, qty 18 (bulletin)
    "GBX-DP-130":  ["dowel", "pin_loc"],                       # locating dowel pin, qty 6
    "GBX-SCS-121": ["capscrw", "zylschraube", "skt_schr"],     # socket cap screw, qty 6
    "GBX-BRG-123": ["roller_brg", "brg_cyl"],                  # cylindrical roller bearing, qty 4
    "GBX-GH-103":  ["helix_gr", "schraegverz", "hel_gear"],    # helical reduction gear, qty 6
    "GBX-OS-124":  ["oilseal", "simmerring", "wellendicht"],   # radial oil seal, qty 4
    "GBX-NB-129":  ["nadellager", "needle_brg"],                # needle roller bearing, qty 6
}

# name patterns that mean "not a trustworthy distinct part" -- weak signal, not deleted
SUSPECT_PATTERNS = [
    r"_old", r"donotuse", r"\(2\)", r"_copy", r"_final_final",
    r"^object\.", r"^solid_", r"^mesh_[0-9a-f]{6}", r"^body_x\d",
]

def family_of(name):
    n = name.lower()
    for pn, tokens in FAMILY_TOKENS.items():
        if any(t in n for t in tokens):
            return pn
    return None

def is_suspect(name):
    n = name.lower()
    return any(re.search(p, n) for p in SUSPECT_PATTERNS)

canonical = json.load(open("canonical_parts.json"))["parts"]

report = {}
for cluster_key, members in clusters.items():
    by_family = {}
    unassigned = []
    for m in members:
        fam = family_of(m)
        suspect = is_suspect(m)
        entry = {"mesh_id": m, "suspect": suspect}
        if fam:
            by_family.setdefault(fam, []).append(entry)
        else:
            unassigned.append(entry)

    families_report = {}
    for pn, entries in by_family.items():
        confident = [e["mesh_id"] for e in entries if not e["suspect"]]
        suspect = [e["mesh_id"] for e in entries if e["suspect"]]
        expected_qty = canonical.get(pn, {}).get("qty")
        families_report[pn] = {
            "desc": canonical.get(pn, {}).get("desc"),
            "expected_qty_canonical": expected_qty,
            "total_members_in_cluster": len(entries),
            "confident_members": confident,
            "suspect_members": suspect,
            "count_matches_catalog": len(entries) == expected_qty,
            "needs_vision_check": len(suspect) > 0 or len(entries) != expected_qty,
        }

    report[cluster_key] = {
        "matched_families": families_report,
        "unassigned_generic_names": [e["mesh_id"] for e in unassigned],
    }

with open("verified_clusters.json", "w") as f:
    json.dump(report, f, indent=2)

# ---- human-readable summary ----
print("=" * 70)
for cluster_key, data in report.items():
    print(f"\nCluster {cluster_key}:")
    for pn, fr in data["matched_families"].items():
        flag = "  << CHECK" if fr["needs_vision_check"] else "  OK"
        print(f"  {pn} ({fr['desc']}): {fr['total_members_in_cluster']} found, "
              f"{fr['expected_qty_canonical']} expected{flag}")
        if fr["suspect_members"]:
            print(f"      suspect: {fr['suspect_members']}")
    if data["unassigned_generic_names"]:
        print(f"  UNASSIGNED (no name signal, geometry-only): {data['unassigned_generic_names']}")
print("\n" + "=" * 70)
print(f"Wrote verified_clusters.json")
