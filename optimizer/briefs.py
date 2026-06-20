#!/usr/bin/env python3
"""
cc-optimizer - build compact per-project briefs for the LLM refinement layer.

The refinement agents must NEVER read the raw 2.6GB of .jsonl transcripts --
only these small aggregate briefs + a compact knowledge base. This keeps the
refinement cheap and bounded. Tier A/B projects get a brief; tier C is skipped.

  python3 briefs.py <raw-stats.json> <cost-stats.json> <findings.json> \
                    <knowledge-base.json> <out_dir>
Writes: <out_dir>/kb-compact.json, <out_dir>/briefs/<slug>.json, <out_dir>/manifest.json
"""
import sys, json, os


def disp(slug):
    s = slug
    if "sources-" in s:
        s = s.split("sources-")[-1]
    else:
        s = s.strip("-").split("-")[-1]
    return s


def main():
    raw = json.load(open(sys.argv[1]))
    cost = json.load(open(sys.argv[2]))
    findings = json.load(open(sys.argv[3]))
    kb = json.load(open(sys.argv[4]))
    out_dir = sys.argv[5]
    bdir = os.path.join(out_dir, "briefs")
    os.makedirs(bdir, exist_ok=True)

    # compact KB: just what an agent needs to validate/adjust a config knob
    kb_compact = {
        "pricing_models": [
            {"model": m["model"], "input_per_mtok": m.get("input_per_mtok"),
             "output_per_mtok": m.get("output_per_mtok"), "cache_read_per_mtok": m.get("cache_read_per_mtok")}
            for m in (kb.get("pricing") or {}).get("models", [])
        ],
        "pricing_ratios": (kb.get("pricing") or {}).get("ratios"),
        "actionable_knobs": [
            {"name": k.get("name"), "config_location": k.get("config_location"), "saves": k.get("saves")}
            for k in kb.get("actionable_knobs", [])
        ],
        "detection_signals": [
            {"signal": s.get("signal"), "maps_to_knob": s.get("maps_to_knob")}
            for s in kb.get("detection_signals", [])
        ],
    }
    json.dump(kb_compact, open(os.path.join(out_dir, "kb-compact.json"), "w"), indent=2, ensure_ascii=False)

    manifest = []
    for name, fp in findings["projects"].items():
        tier = fp["tier"]
        if tier == "C-skip":
            continue
        rp = raw["projects"].get(name, {})
        cp = cost["projects"].get(name, {})
        # compact model breakdown: tokens + $ per model
        by_model = {}
        for m, b in rp.get("by_model", {}).items():
            by_model[m] = {
                "msgs": b.get("msgs", 0),
                "billable": b.get("input_tokens", 0) + b.get("output_tokens", 0)
                + b.get("cache_creation_input_tokens", 0) + b.get("cache_read_input_tokens", 0),
                "tiny_msgs": b.get("tiny_msgs", 0),
                "usd": cp.get("by_model_usd", {}).get(m, 0),
            }
        brief = {
            "project": name,
            "name": disp(name),
            "tier": tier,
            "estimated_usd": fp["estimated_usd"],
            "estimated_savings_usd": fp["estimated_savings_usd"],
            "sessions": rp.get("sessions", 0),
            "assistant_msgs": rp.get("assistant_msgs", 0),
            "cache_hit_ratio": rp.get("cache_hit_ratio", 0),
            "ephemeral_1h_input_tokens": rp.get("ephemeral_1h_input_tokens", 0),
            "by_model": by_model,
            "sub_by_model": rp.get("sub_by_model", {}),
            "distinct_mcp_servers": rp.get("distinct_mcp_servers", 0),
            "mcp_calls": dict(list(rp.get("mcp_calls", {}).items())[:10]),
            "base_context_median": rp.get("base_context_median", 0),
            "base_context_max": rp.get("base_context_max", 0),
            "web_search_requests": rp.get("web_search_requests", 0),
            "web_fetch_requests": rp.get("web_fetch_requests", 0),
            "versions": rp.get("versions", {}),
            "deterministic_findings": fp["findings"],
        }
        safe = name.strip("-").replace("/", "_")[:120]
        path = os.path.join(bdir, safe + ".json")
        json.dump(brief, open(path, "w"), indent=2, ensure_ascii=False)
        manifest.append({"project": name, "name": disp(name), "tier": tier, "path": path,
                         "estimated_savings_usd": fp["estimated_savings_usd"]})

    manifest.sort(key=lambda x: -x["estimated_savings_usd"])
    json.dump(manifest, open(os.path.join(out_dir, "manifest.json"), "w"), indent=2, ensure_ascii=False)
    a = sum(1 for m in manifest if m["tier"] == "A-strong")
    b = sum(1 for m in manifest if m["tier"] == "B-cheap")
    print("briefs written: %d (tier A=%d -> Opus, tier B=%d -> Sonnet)" % (len(manifest), a, b))
    print("kb-compact: %d knobs, %d signals, %d pricing rows" % (
        len(kb_compact["actionable_knobs"]), len(kb_compact["detection_signals"]), len(kb_compact["pricing_models"])))
    print("manifest:", os.path.join(out_dir, "manifest.json"))


if __name__ == "__main__":
    main()
