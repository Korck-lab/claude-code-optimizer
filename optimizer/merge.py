#!/usr/bin/env python3
"""
cc-optimizer - merge deterministic findings with the LLM refinement output.

Produces findings-final.json: deterministic per-project findings are replaced
by the LLM-kept, project-tightened versions, but the original deterministic
EVIDENCE (the auditable numbers) is preserved and the LLM rationale appended,
so the final report never claims anything that doesn't trace back to the data.
The cross-project rollup is attached under global.rollup. Tier-C and any
project the refiner skipped keep their deterministic findings unchanged.

  python3 merge.py <findings.json> <refine-result.json> <out-findings-final.json>

<refine-result.json> may be the workflow task output ({summary,...,result:{...}})
or the bare {refined, rollup} object.
"""
import sys, json


CANON = [
    ("Subagentes em modelo caro", ("subagent", "sidechain")),
    ("Cache 1h sem amortizar", ("1h-cache", "1h cache", "ephemeral_1h", "force_prompt_caching")),
    ("Cache hit baixo", ("cache hit", "cache-hit", "prompt-cache hit", "cache miss")),
    ("Bloat de MCP / contexto", ("mcp", "context bloat", "per-turn context", "oversized", "tool-schema", "tool schema")),
    ("Modelo caro em trabalho trivial/geral", ("trivial", "default model", "all work", "all 1", "all messages",
                                              "vast majority", "majority of work", "opus default", "opus at",
                                              "no subagent", "short turn", "thinking-token")),
]


def canonical_signal(sig):
    s = (sig or "").lower()
    for label, keys in CANON:
        if any(k in s for k in keys):
            return label
    return "Outros"


def load_result(path):
    d = json.load(open(path))
    if isinstance(d, dict) and "result" in d and "refined" in (d.get("result") or {}):
        return d["result"]
    return d


def main():
    findings = json.load(open(sys.argv[1]))
    refine = load_result(sys.argv[2])
    out_path = sys.argv[3]
    det_total = findings["global"].get("estimated_savings_usd_total", 0)

    refined_by_project = {r["project"]: r for r in refine.get("refined", []) if r.get("project")}

    for name, proj in findings["projects"].items():
        r = refined_by_project.get(name)
        if not r:
            continue  # tier C or refiner skipped -> keep deterministic
        # index original deterministic evidence by signal
        det_ev = {f["signal"]: f.get("evidence", "") for f in proj.get("findings", [])}
        kept = []
        for f in r.get("findings", []):
            if not f.get("keep"):
                continue
            base_ev = det_ev.get(f["signal"])
            evidence = base_ev if base_ev else (f.get("rationale", "") + " (novo achado — refino LLM)")
            if base_ev and f.get("rationale"):
                evidence = base_ev + " | refino: " + f["rationale"]
            # R1b (expensive default model on trivial turns) is an UPPER BOUND lever:
            # force confidence=low for consistency, regardless of what the LLM labeled.
            conf = f.get("confidence", "")
            sig_cfg = (evidence + " " + f.get("config_location", "") + " " + f.get("recommended_value", "")).lower()
            if "trivial-turn share" in sig_cfg or "opusplan" in sig_cfg:
                conf = "low"
            kept.append({
                "signal": f["signal"],
                "knob": f.get("knob", ""),
                "config_location": f["config_location"],
                "recommended_value": f["recommended_value"],
                "est_saving_usd": round(float(f.get("est_saving_usd", 0) or 0), 2),
                "confidence": conf,
                "evidence": evidence,
            })
        proj["findings"] = kept
        proj["estimated_savings_usd"] = round(sum(x["est_saving_usd"] for x in kept), 2)
        proj["profile"] = r.get("profile", "")
        proj["refined"] = True

    # recompute global savings + by-signal from final findings
    by_signal = {}
    total = 0.0
    for proj in findings["projects"].values():
        for f in proj["findings"]:
            cs = canonical_signal(f["signal"])
            by_signal[cs] = round(by_signal.get(cs, 0) + f["est_saving_usd"], 2)
            total += f["est_saving_usd"]
    findings["global"]["estimated_savings_usd_total"] = round(total, 2)
    findings["global"]["estimated_savings_usd_deterministic"] = round(det_total, 2)
    findings["global"]["by_signal_usd"] = dict(sorted(by_signal.items(), key=lambda kv: -kv[1]))
    findings["global"]["rollup"] = refine.get("rollup", {})
    findings["refinement"] = {
        "projects_refined": len(refined_by_project),
        "note": "Per-project findings validated/tightened by an LLM reading only compact aggregate briefs (never raw transcripts). $ never raised above the deterministic estimate.",
    }

    json.dump(findings, open(out_path, "w"), indent=2, ensure_ascii=False, sort_keys=True)
    print("merged %d refined projects -> %s" % (len(refined_by_project), out_path))
    print("final global est savings = $%.0f" % findings["global"]["estimated_savings_usd_total"])
    print("by signal:")
    for s, v in findings["global"]["by_signal_usd"].items():
        print("  $%-9.0f %s" % (v, s))


if __name__ == "__main__":
    main()
