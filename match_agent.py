"""
match_agent.py v2 — step-to-part-to-mesh matching for the gearbox service manual.

Pipeline:
  1. Load service_steps.json (repair procedure steps, step_id-keyed)
  2. Load canonical_parts.json (parts catalog + bulletin step_corrections)
  3. Load matched_clusters.json (part_id -> candidate mesh_ids, pre-matched by
     mesh name to part description — this script does NOT re-derive mesh
     matches, it only decides which parts/mesh-groups are relevant to which
     repair step)
  4. For every step, score every part's text against the step's text
     (title + instruction + bulletin correction if any) and keep parts above
     threshold as candidates.
  5. One Ollama vision call per step: show renders for that step's candidate
     mesh_ids (drawn from matched_clusters.json) and ask the model to confirm/
     reject/rank them against the step description.
  6. Write step_mesh_matches.json: {step_id: {part_id: {mesh_ids, confidence, reasoning}}}

Run with --dry-run to skip the Ollama calls entirely and just inspect the
candidate-selection step (useful for tuning the scorer).
"""
import os, re, json, base64, io, argparse, requests
from PIL import Image

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5vl:7b"

# Generic stopwords for shop-manual English. Deliberately does NOT include
# domain words (bolt, ring, bearing, etc.) since those carry real signal.
STOPWORDS = set("""
a an the of on in to for and or with without from at by as is are was were
be been being this that these those it its into onto than then so not no
do does did per see fig figs ref refit torque approx max min mm kg nm degc
lb note caution warning above below both all each new old
""".split())


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def stem(word):
    """Naive plural stemmer so 'bolts' matches part-name token 'bolt'.
    Deliberately conservative (shop-manual vocabulary, not general English):
    strips a trailing 's' unless the word ends 'ss' or 'us', or is <=3 chars,
    so 'bolts'->'bolt', 'washers'->'washer', 'bearings'->'bearing', while
    'class'/'bus' are left alone."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss") and not word.endswith("us"):
        return word[:-1]
    return word


def tokenize(text):
    """Lowercase word tokens, letters-first (so bare numbers/units are
    dropped), stopwords removed, length > 2, pluralization normalized."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9]*", text or "")
    return [stem(w.lower()) for w in words if w.lower() not in STOPWORDS and len(w) > 2]


def dedupe(tokens):
    """Preserve order, drop repeats. Repeated words in paragraph-length step
    instructions (e.g. 'bolts' said 4x) shouldn't inflate the token count —
    a step either mentions a concept or it doesn't."""
    return list(dict.fromkeys(tokens))


def load_step_corrections(canonical_parts):
    return canonical_parts.get("step_corrections", {})


def annotate_with_corrections(step, step_corrections):
    """Human/vision-model-facing text: instruction + correction, wrapped with
    an explicit 'this overrides the manual' framing. NOT used for scoring —
    see correction_content_for_scoring below."""
    text = step.get("instruction", "")
    corr = step_corrections.get(step["step_id"])
    if corr:
        text += (
            f" CORRECTION per service bulletin (authoritative, overrides manual "
            f"text above): was {corr.get('was', '')}. Now: {corr.get('is', '')}. "
            f"{corr.get('note', '')}"
        )
    return text


def scoring_text_for_step(step, step_corrections):
    """Text used for candidate scoring: the raw instruction plus ONLY the
    correction's actual content fields (was/is/note) — no wrapper phrasing.
    Earlier version reused annotate_with_corrections() for scoring too, which
    meant my own template words ('correction', 'authoritative', 'overrides',
    'bulletin', 'manual', 'text', 'above', 'now'...) were injected into every
    corrected step and diluted the real signal by ~8-9 tokens every time —
    that, not pluralization, was the actual cause of the HXB-122/20-30 miss."""
    text = step.get("instruction", "")
    corr = step_corrections.get(step["step_id"])
    if corr:
        text += f" {corr.get('was', '')} {corr.get('is', '')} {corr.get('note', '')}"
    return text


def build_part_search_tokens(parts):
    """
    For every part, build two token sets:
      - name_tokens: from the part description + part number. These are the
        highest-signal words ("bolt", "hex", "cover", "dowel", "pin"...).
      - all_tokens: name_tokens plus sub-assembly tokens ("A5-Fasteners" etc.)
        for broader (lower-confidence) matching.
    Keeping these separate is what lets score_text_against_part weight an
    exact part-name hit higher than an incidental word shared with a long
    instruction paragraph.
    """
    out = {}
    for pn, p in parts.items():
        desc_tokens = tokenize(p.get("desc", ""))
        pn_tokens = tokenize(pn.replace("-", " "))
        name_tokens = set(desc_tokens + pn_tokens)
        sub_tokens = set(tokenize(p.get("sub_asm", "")))
        out[pn] = {
            "name_tokens": name_tokens,
            "all_tokens": name_tokens | sub_tokens,
        }
    return out


def score_text_against_part(step_tokens, part_entry, name_weight=4.0):
    """
    Weighted coverage score in [0, ~1]. Every (deduped) step token counts
    once toward the denominator. If that token is a part NAME token (e.g.
    "bolt" for GBX-HXB-122 "Cover Hex Bolt"), it counts `name_weight` (2.5x)
    toward both numerator and denominator; if it's only a broader sub-
    assembly token, it counts 1x; if it's not in the part's vocabulary at
    all, it only adds to the denominator (drags the score down).

    This is the fix for the 20-30 / HXB-122 bug: previously every token in a
    long paragraph (including boilerplate like "authoritative", "predates",
    "boss") counted equally, so a few real hits like "bolt"/"bolts" got
    diluted below threshold. Now exact name-token hits are weighted enough
    to survive that dilution, while boilerplate still contributes to (but no
    longer dominates) the denominator.
    """
    if not step_tokens:
        return 0.0
    name_tokens = part_entry["name_tokens"]
    all_tokens = part_entry["all_tokens"]
    numerator = 0.0
    denominator = 0.0
    for t in step_tokens:
        if t in name_tokens:
            numerator += name_weight
            denominator += name_weight
        elif t in all_tokens:
            numerator += 1.0
            denominator += 1.0
        else:
            denominator += 1.0
    return numerator / denominator if denominator else 0.0


def candidates_for_step(step, step_corrections, parts, part_search_tokens, threshold=0.15, top_n=3):
    """Return [(part_id, score), ...] sorted desc, above threshold, capped at
    top_n — except top_n is a soft cap: if the score at the cutoff is tied
    with candidates just past it, all tied candidates are kept. Otherwise a
    plain top_n slice can arbitrarily drop the right part when several
    parts land on the same score (e.g. step 20-30 has 5 candidates tied at
    0.16, one of which — GBX-HXB-122 — is the one we specifically fixed the
    scorer to surface; cutting blind at 3 would silently break that again)."""
    full_text = step["title"] + " " + scoring_text_for_step(step, step_corrections)
    step_tokens = dedupe(tokenize(full_text))

    scored = []
    for pn in parts:
        s = score_text_against_part(step_tokens, part_search_tokens[pn])
        if s >= threshold:
            scored.append((pn, round(s, 4)))
    scored.sort(key=lambda x: -x[1])

    if len(scored) <= top_n:
        return scored, step_tokens
    cutoff_score = scored[top_n - 1][1]
    kept = [c for c in scored if c[1] >= cutoff_score]
    return kept, step_tokens


# ---------------------------------------------------------------------------
# Vision confirmation (one Ollama call per step, over that step's candidates)
# ---------------------------------------------------------------------------

def img_b64(path, max_dim=500):
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def render_path_for_part(renders_dir, part_id):
    for fname in (f"family_{part_id}.png", f"family_{part_id}_v2.png"):
        p = os.path.join(renders_dir, fname)
        if os.path.exists(p):
            return p
    return None


def strip_code_fence(raw):
    """Python-3.8-compatible stand-in for str.removeprefix/removesuffix
    (those methods are 3.9+). Strips optional ```json / ``` fences the model
    sometimes wraps its JSON reply in."""
    s = raw.strip()
    if s.startswith("```json"):
        s = s[len("```json"):]
    elif s.startswith("```"):
        s = s[len("```"):]
    if s.endswith("```"):
        s = s[: -len("```")]
    return s.strip()


def confirm_step_with_vision(step, step_corrections, candidates, clusters, renders_dir):
    """
    One Ollama call for this step: shows the family-view render (green/red/
    yellow highlighted) for every candidate part, asks the model to confirm
    which are actually involved in the step and cite mesh_ids from the
    pre-checked candidate list only (never invent an id).
    """
    images_b64 = []
    lines = []
    for pn, score in candidates:
        rp = render_path_for_part(renders_dir, pn)
        cluster = clusters.get(pn, {})
        mesh_ids = cluster.get("mesh_ids", [])
        desc = cluster.get("description", "")
        if rp and mesh_ids:
            images_b64.append(img_b64(rp))
            lines.append(
                f"- {pn} ({desc}), text-score={score}, candidate mesh_ids={mesh_ids}"
            )

    if not images_b64:
        return {"mesh_matches": {}, "reasoning": "no renderable candidates with mesh_ids"}

    step_text = step["title"] + ". " + annotate_with_corrections(step, step_corrections)
    candidate_block = "\n".join(lines)

    prompt = f"""
You are confirming which parts are involved in ONE repair step of a gearbox
service manual, using renders of each CANDIDATE part already highlighted in
its 3D model.

Step {step['step_id']}: {step_text}

Candidate parts (in the same order as the images), each with the ONLY
mesh_ids you are allowed to cite for that part:
{candidate_block}

For each candidate part, decide: is it actually involved in this step?
Respond ONLY with valid JSON, no other text, in this exact shape:
{{"GBX-XXX-000": {{"involved": true, "mesh_ids": ["..."], "confidence": "high|medium|low", "reasoning": "..."}}}}
Only include mesh_ids that were listed for that part above — never invent one.
"""
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt, "images": images_b64}],
            "stream": False,
            "options": {"temperature": 0.1},
        },
    )
    raw = resp.json()["message"]["content"]
    cleaned = strip_code_fence(raw)
    return json.loads(cleaned)


# ---------------------------------------------------------------------------

def build_candidate_record(step, candidates, parts):
    """
    Structured, JSON-friendly view of the candidate-scoring stage for one
    step: which part scored highest, its description (i.e. what physical
    part the step is telling the technician to remove/service), and the
    score — plus the full ranked candidate list for context.
    Returns None for top_part/top_score/top_description if no candidate
    cleared threshold for this step.
    """
    ranked = [
        {"part_id": pn, "description": parts[pn]["desc"], "score": score}
        for pn, score in candidates
    ]
    top = ranked[0] if ranked else None
    return {
        "step_id": step["step_id"],
        "step_title": step["title"],
        "top_part_id": top["part_id"] if top else None,
        "part_to_remove": top["description"] if top else None,
        "top_score": top["score"] if top else None,
        "candidates": ranked,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="service_steps.json")
    ap.add_argument("--parts", default="canonical_parts.json")
    ap.add_argument("--clusters", default="matched_clusters.json")
    ap.add_argument("--renders", default="renders")
    ap.add_argument("--out", default="step_mesh_matches.json")
    ap.add_argument("--candidates-out", default="step_candidates.json",
                     help="JSON dump of the scoring stage: top part_id, its description, and score per step")
    ap.add_argument("--threshold", type=float, default=0.15)
    ap.add_argument("--top-n", type=int, default=3, help="max candidates kept per step (default 3)")
    ap.add_argument("--dry-run", action="store_true", help="candidate selection only, no Ollama calls")
    args = ap.parse_args()

    steps = load_json(args.steps)["steps"]
    canonical = load_json(args.parts)
    parts = canonical["parts"]
    step_corrections = load_step_corrections(canonical)
    clusters = load_json(args.clusters)["clusters"] if os.path.exists(args.clusters) else {}

    part_search_tokens = build_part_search_tokens(parts)

    results = {}
    candidate_records = {}

    def save_progress():
        with open(args.candidates_out, "w") as f:
            json.dump(candidate_records, f, indent=2)
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)

    for step in steps:
        candidates, step_tokens = candidates_for_step(
            step, step_corrections, parts, part_search_tokens, threshold=args.threshold, top_n=args.top_n
        )
        print(f"\nStep {step['step_id']} — {step['title']}")
        print(f"  tokens: {step_tokens}")
        for pn, score in candidates:
            print(f"  candidate: {pn:14s} score={score}  ({parts[pn]['desc']})")

        candidate_records[step["step_id"]] = build_candidate_record(step, candidates, parts)

        if args.dry_run:
            results[step["step_id"]] = {"candidates": candidates}
            save_progress()
            continue

        try:
            confirmed = confirm_step_with_vision(step, step_corrections, candidates, clusters, args.renders)
        except Exception as e:
            # A single bad/slow/non-JSON Ollama response shouldn't wipe out
            # every step already completed — log it and keep going.
            print(f"  !! vision confirmation failed for {step['step_id']}: {e}")
            confirmed = {"error": str(e)}
        results[step["step_id"]] = confirmed
        save_progress()
        print(f"  (progress saved: {args.candidates_out}, {args.out})")

    print(f"\nDone. Saved {args.candidates_out} and {args.out}")


if __name__ == "__main__":
    main()