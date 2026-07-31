"""
Stage 0 (new): Reconcile IPC catalogue + SB-2019-04 bulletin + parts_xref +
work order into ONE canonical parts table before any 3D matching happens.

Why this has to run first: the catalogue, manual, and bulletin disagree with
each other on qty/torque/status for several parts. SB-2019-04 is authoritative
per its own header. Feeding the VLM a self-consistent parts table (instead of
three conflicting source docs) is what makes fine-grained matching possible.

Output: canonical_parts.json -- one row per OEM part number, bulletin-corrected,
unit-normalized, with supplier PN and disposition history attached.
"""
import json
import csv
import re

OUT_PATH = "canonical_parts.json"

# ---- 1. Base catalogue extracted from IPC-GBX-450E rev.B Section 2 BOM ----
# (manually transcribed from the PDF table -- item#, PN, description, sub-asm,
# qty, material. Envelope/units not needed downstream, dropped here.)
catalogue = [
    {"pn": "GBX-BH-128",  "desc": "Bearing Housing Boss",        "sub_asm": "A0-Housing", "qty": 2,  "material": "Cast iron"},
    {"pn": "GBX-BRP-120", "desc": "Breather / Filler Plug",       "sub_asm": "A0-Housing", "qty": 1,  "material": "Brass CW614N"},
    {"pn": "GBX-HSG-100", "desc": "Gearbox Housing Half",         "sub_asm": "A0-Housing", "qty": 2,  "material": "Cast iron GG-25"},
    {"pn": "GBX-MF-126",  "desc": "Mounting Flange",              "sub_asm": "A0-Housing", "qty": 2,  "material": "EN-GJS-500"},
    {"pn": "GBX-SRP-119", "desc": "Seal Retainer Plate",          "sub_asm": "A0-Housing", "qty": 2,  "material": "S235JR"},
    {"pn": "GBX-EC-127",  "desc": "Shaft End Cap",                "sub_asm": "A0-Housing", "qty": 2,  "material": "EN-GJL-200"},
    {"pn": "GBX-GP-104",  "desc": "Input Pinion",                 "sub_asm": "A1-Input",   "qty": 2,  "material": "20MnCr5, ground"},
    {"pn": "GBX-ISH-116", "desc": "Input Shaft",                  "sub_asm": "A1-Input",   "qty": 1,  "material": "20MnCr5"},
    {"pn": "GBX-CF-117",  "desc": "Coupling Flange",              "sub_asm": "A2-Output",  "qty": 1,  "material": "C45"},
    {"pn": "GBX-OSH-115", "desc": "Output Shaft",                 "sub_asm": "A2-Output",  "qty": 1,  "material": "42CrMo4, hardened"},
    {"pn": "GBX-GLG-102", "desc": "Output Spur Gear",             "sub_asm": "A2-Output",  "qty": 2,  "material": "16MnCr5, carburized"},
    {"pn": "GBX-GB-105",  "desc": "Bevel Gear",                   "sub_asm": "A3-Layshaft","qty": 2,  "material": "17NiCrMo6-4"},
    {"pn": "GBX-GC-107",  "desc": "Cluster Gear",                 "sub_asm": "A3-Layshaft","qty": 2,  "material": "18CrNiMo7-6"},
    {"pn": "GBX-GH-103",  "desc": "Helical Reduction Gear",       "sub_asm": "A3-Layshaft","qty": 6,  "material": "16MnCr5, carburized"},
    {"pn": "GBX-GI-106",  "desc": "Idler Gear",                   "sub_asm": "A3-Layshaft","qty": 2,  "material": "16MnCr5"},
    {"pn": "GBX-GR-101",  "desc": "Internal Ring Gear",           "sub_asm": "A3-Layshaft","qty": 2,  "material": "18CrNiMo7-6, case-hardened"},
    {"pn": "GBX-SH-114",  "desc": "Layshaft / Countershaft",      "sub_asm": "A3-Layshaft","qty": 2,  "material": "42CrMo4, hardened"},
    {"pn": "GBX-BC-108",  "desc": "Bearing Retainer Cap",         "sub_asm": "A4-Bearings","qty": 4,  "material": "EN-GJL-250"},
    {"pn": "GBX-BRG-123", "desc": "Cylindrical Roller Bearing",   "sub_asm": "A4-Bearings","qty": 4,  "material": "Bearing steel 100Cr6"},
    {"pn": "GBX-DS-125",  "desc": "Distance Sleeve",              "sub_asm": "A4-Bearings","qty": 2,  "material": "St 52-3"},
    {"pn": "GBX-NB-129",  "desc": "Needle Roller Bearing",        "sub_asm": "A4-Bearings","qty": 6,  "material": "100Cr6"},
    {"pn": "GBX-OS-124",  "desc": "Radial Oil Seal (Simmerring)", "sub_asm": "A4-Bearings","qty": 4,  "material": "NBR / steel"},
    {"pn": "GBX-TW-109",  "desc": "Thrust Washer",                "sub_asm": "A4-Bearings","qty": 2,  "material": "CuSn8 bronze"},
    {"pn": "GBX-SHM-118", "desc": "Adjusting Shim / Spacer",      "sub_asm": "A5-Fasteners","qty": 1, "material": "Shim steel 1.4310"},
    {"pn": "GBX-HXB-122", "desc": "Cover Hex Bolt (DIN 933)",     "sub_asm": "A5-Fasteners","qty": 16,"material": "Grade 8.8, zinc"},
    {"pn": "GBX-SR-110",  "desc": "External Snap Ring",           "sub_asm": "A5-Fasteners","qty": 2, "material": "Spring steel C67S"},
    {"pn": "GBX-FW-112",  "desc": "Flat Washer (DIN 125)",        "sub_asm": "A5-Fasteners","qty": 2, "material": "A2-70 stainless"},
    {"pn": "GBX-CL-111",  "desc": "Internal Circlip (DIN 472)",   "sub_asm": "A5-Fasteners","qty": 2, "material": "Spring steel"},
    {"pn": "GBX-DP-130",  "desc": "Locating Dowel Pin (DIN 6325)","sub_asm": "A5-Fasteners","qty": 6, "material": "Hardened steel"},
    {"pn": "GBX-KEY-113", "desc": "Parallel Shaft Key (DIN 6885)","sub_asm": "A5-Fasteners","qty": 2, "material": "C45K"},
    {"pn": "GBX-SCS-121", "desc": "Socket Cap Screw (DIN 912)",   "sub_asm": "A5-Fasteners","qty": 6, "material": "Grade 12.9"},
    {"pn": "GBX-SG-131",  "desc": "Oil Level Sight Glass",        "sub_asm": "A0-Housing", "qty": 0,  "material": None},
]

# ---- 2. Envelope unit-error fix: two catalogue rows were printed in cm ----
CM_TO_MM_PARTS = {"GBX-SH-114", "GBX-OSH-115"}  # SB-2019-04 section 2

# ---- 3. Bulletin corrections (authoritative -- overwrite catalogue values) ----
bulletin_corrections = {
    "GBX-HXB-122": {"qty": 18, "note": "2 added at casting rev.E, front flange. Was 16 in IPC rev.B."},
    "GBX-OS-124":  {"superseded_by": "GBX-OS-124-B", "note": "FKM lip, same bore/envelope, physically interchangeable"},
    "GBX-SG-131":  {"qty": 0, "note": "NOT FITTED on -E variants -- inspect drained oil instead"},
}

# Step-level corrections (not part-qty, but affects mapping step 20-30's bolt count)
step_corrections = {
    "20-30": {"was": "both (2) mid-ring bolts", "is": "three (3) mid-ring bolts, drive end",
              "note": "1998 text predates third boss added at casting rev.E"},
    "30-10": {"was": "one bolt has bonded washer", "is": "bonded washer deleted 2007, all 6 identical"},
    "10-10": {"was": "check sight glass", "is": "sight glass NOT FITTED on -E, inspect drained oil instead"},
}

torque_schedule = {
    "GBX-HXB-122": "9.5 Nm",
    "GBX-SCS-121": "13 Nm",
    "GBX-BRP-120": "25 Nm, sealant on 2nd thread",
}

# ---- 4. Load supplier cross-reference ----
xref_by_pn = {}
with open("/mnt/user-data/uploads/parts_xref.csv") as f:
    for row in csv.DictReader(f):
        xref_by_pn.setdefault(row["oem_pn"], []).append({
            "supplier": row["supplier"],
            "supplier_pn": row["supplier_pn"],
            "din_ref": row["din_ref"],
            "status_note": row["status_note"],
        })

# ---- 5. Clean inspection log (drop void/unknown rows, normalize dates) ----
def normalize_date(d):
    d = d.strip()
    for fmt_pat in [r"^(\d{4})-(\d{2})-(\d{2})$", r"^(\d{2})/(\d{2})/(\d{4})$", r"^(\d{2})\.(\d{2})\.(\d{4})$"]:
        m = re.match(fmt_pat, d)
        if m:
            g = m.groups()
            if len(g[0]) == 4:
                return f"{g[0]}-{g[1]}-{g[2]}"
            return f"{g[2]}-{g[1]}-{g[0]}"
    return d

inspection_by_pn = {}
with open("/mnt/user-data/uploads/inspection_log.csv") as f:
    for row in csv.DictReader(f):
        if row["disposition"] in ("VOID ROW",) or row["part_no"] in ("GBX-XX-000", "GBX-ZZ-999"):
            continue  # dirty rows -- not real parts
        row["date"] = normalize_date(row["date"])
        inspection_by_pn.setdefault(row["part_no"], []).append(row)

# ---- 6. Load work order consumption (supplier PNs -> resolve to OEM via xref) ----
supplier_to_oem = {}
for oem_pn, entries in xref_by_pn.items():
    for e in entries:
        supplier_to_oem[e["supplier_pn"]] = oem_pn

work_order_consumption = {
    # line: supplier_pn, qty, disposition, step refs -- transcribed from WO-7741
    "DFT-BA-20x35x7-FKM": {"qty": 1, "disposition": "fitted, drive-end front bore", "steps": ["20-40"]},
    "KGW-HK1210":         {"qty": 6, "disposition": "all cage positions renewed", "steps": ["30-60"]},
    "NRM-472-I22":        {"qty": 2, "disposition": "renewed on reassembly", "steps": ["20-60"]},
    "GLT-AS2035-CuSn8":   {"qty": 2, "disposition": "renewed on reassembly", "steps": ["20-60"]},
    "NRM-471-A20":        {"qty": 2, "disposition": "renewed", "steps": ["30-70"]},
    "GLT-PS20-015":       {"qty": 1, "disposition": "end-float shim, 0.15mm selected", "steps": ["30-70"]},
    "GLT-DH20-28":        {"qty": 2, "disposition": "inspected, re-used, qty 0 billed", "steps": ["30-70"]},
    "NRM-933-M6x16-8.8":  {"qty": 15, "disposition": "renewed where disturbed (full 18 recommended, declined)", "steps": ["10-20", "20-30", "30-10"]},
    "NRM-912-M5x20-12.9": {"qty": 6, "disposition": "renewed", "steps": ["30-20"]},
}

# ---- 7. Build canonical table ----
canonical = {}
for part in catalogue:
    pn = part["pn"]
    entry = dict(part)
    entry["unit_note"] = "corrected cm->mm per SB-2019-04" if pn in CM_TO_MM_PARTS else None

    if pn in bulletin_corrections:
        corr = bulletin_corrections[pn]
        if "qty" in corr:
            entry["qty_catalogue_original"] = entry["qty"]
            entry["qty"] = corr["qty"]
        entry["bulletin_note"] = corr.get("note")
        if "superseded_by" in corr:
            entry["superseded_by"] = corr["superseded_by"]

    entry["torque"] = torque_schedule.get(pn)
    entry["suppliers"] = xref_by_pn.get(pn, [])
    entry["inspection_records"] = inspection_by_pn.get(pn, [])

    consumed = []
    for supplier_pn, wo in work_order_consumption.items():
        if supplier_to_oem.get(supplier_pn) == pn:
            consumed.append({"supplier_pn": supplier_pn, **wo})
    entry["work_order_WO-7741_consumption"] = consumed

    canonical[pn] = entry

with open(OUT_PATH, "w") as f:
    json.dump({
        "source_priority": "SB-2019-04 > IPC-GBX-450E rev.B > WM-GBX-450E rev.C (per bulletin header)",
        "step_corrections": step_corrections,
        "parts": canonical,
    }, f, indent=2)

print(f"Wrote {len(canonical)} reconciled parts -> {OUT_PATH}")
print(f"Bulletin-corrected parts: {list(bulletin_corrections.keys())}")
print(f"Unit-fixed (cm->mm) parts: {sorted(CM_TO_MM_PARTS)}")
