#!/usr/bin/env python3
"""
cc-optimizer - deterministic recommendation engine ("rules engine").

Turns per-project aggregates (raw-stats.json) + pricing/knob catalog
(knowledge-base.json, from fresh official docs) into per-project ACTIONABLE
findings. Each finding carries: the measured signal, the exact config knob
to apply (with its doc-sourced location), a concrete recommended value for
THIS project, an estimated $ saving derived from the real token components,
a confidence, and the evidence (the numbers + formula) so it is auditable.

Purely advisory tips are never emitted -- only findings that map to an
applyable config knob from the KB. No network, no model tokens.

  python3 recommend.py <raw-stats.json> <knowledge-base.json> <out-findings.json> \
                       [--floor USD] [--top-n N]

Output also routes each project to a synthesis tier:
  A (top-N by $)            -> refine with a stronger model
  B (>= floor)              -> refine with a cheaper model
  C (< floor)               -> below tuning threshold; apply global defaults
"""
import sys, json, datetime

MIN_FINDING_USD = 5.0       # don't emit a config change worth less than this
TARGET_CACHE_HIT = 0.92     # healthy cache-hit baseline (global median observed)
LEAN_BASE_CONTEXT = 15000   # a lean per-turn fixed overhead (tokens)


def family(model_id):
    m = (model_id or "").lower()
    for fam in ("opus", "sonnet", "haiku", "fable", "mythos"):
        if fam in m:
            return fam
    return None


def build_rates(models):
    idx = {}
    for row in models:
        fam = family(row.get("model"))
        if not fam or fam in idx:
            continue
        inp = float(row.get("input_per_mtok") or 0)
        idx[fam] = {
            "in": inp,
            "out": float(row.get("output_per_mtok") or 0),
            "cw5": float(row.get("cache_write_5m_per_mtok") or inp * 1.25),
            "cw1": float(row.get("cache_write_1h_per_mtok") or inp * 2.0),
            "crd": float(row.get("cache_read_per_mtok") or inp * 0.1),
        }
    return idx


def comp_of(b):
    cc = b.get("cache_creation_input_tokens", 0)
    eph1 = b.get("ephemeral_1h_input_tokens", 0)
    eph5 = b.get("ephemeral_5m_input_tokens", 0)
    lo = max(0, cc - eph1 - eph5)
    return {
        "in": b.get("input_tokens", 0), "out": b.get("output_tokens", 0),
        "eph1": eph1, "eph5": eph5, "lo": lo, "cr": b.get("cache_read_input_tokens", 0),
    }


def comp_cost(c, r):
    return (c["in"] / 1e6 * r["in"] + c["out"] / 1e6 * r["out"]
            + c["eph1"] / 1e6 * r["cw1"] + (c["eph5"] + c["lo"]) / 1e6 * r["cw5"]
            + c["cr"] / 1e6 * r["crd"])


def billable_of(c):
    return c["in"] + c["out"] + c["eph1"] + c["eph5"] + c["lo"] + c["cr"]


def lump_saving(lump, ref_bucket, r_from, r_to):
    """Saving from moving `lump` billable tokens (with ref_bucket's mix) from
    r_from to r_to rates."""
    c = comp_of(ref_bucket)
    bill = billable_of(c)
    if bill <= 0 or lump <= 0:
        return 0.0
    s = lump / bill
    sc = {k: v * s for k, v in c.items()}
    return max(0.0, comp_cost(sc, r_from) - comp_cost(sc, r_to))


EXPENSIVE = ("opus", "fable", "mythos")


def dominant_rate(by_model, rates):
    best, best_bill = rates.get("opus"), -1
    for m, b in by_model.items():
        fam = family(m)
        if fam in rates:
            bill = billable_of(comp_of(b))
            if bill > best_bill:
                best_bill, best = bill, rates[fam]
    return best or {"in": 5, "out": 25, "cw5": 6.25, "cw1": 10, "crd": 0.5}


def project_findings(p, rates):
    findings = []
    by_model = p.get("by_model", {})
    sub_by_model = p.get("sub_by_model", {})
    haiku = rates.get("haiku", {"in": 1, "out": 5, "cw5": 1.25, "cw1": 2, "crd": 0.1})
    sonnet = rates.get("sonnet", {"in": 3, "out": 15, "cw5": 3.75, "cw1": 6, "crd": 0.3})

    # R1a: subagents running on expensive models -> CLAUDE_CODE_SUBAGENT_MODEL=haiku
    sub_save = 0.0
    sub_detail = {}
    for m, lump in sub_by_model.items():
        if family(m) in EXPENSIVE and family(m) in rates:
            s = lump_saving(lump, by_model.get(m, {}), rates[family(m)], haiku)
            if s > 0:
                sub_save += s
                sub_detail[m] = {"sub_billable": lump, "save_usd": round(s, 2)}
    if sub_save >= MIN_FINDING_USD:
        findings.append({
            "signal": "Subagents running on expensive models",
            "knob": "CLAUDE_CODE_SUBAGENT_MODEL",
            "config_location": '.claude/settings.json -> {"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}}',
            "recommended_value": "haiku (or sonnet for code-writing subagents)",
            "est_saving_usd": round(sub_save, 2),
            "confidence": "high",
            "evidence": "Subagent (isSidechain) billable tokens on expensive models: "
                        + ", ".join("%s=%d" % (k.split('claude-')[-1], v["sub_billable"]) for k, v in sub_detail.items())
                        + ". Saving = retariff those tokens to Haiku at the model's own component mix.",
        })

    # R1b: trivial-turn share on expensive models -> default model downgrade.
    # The default "model" knob is COARSE: it changes EVERY turn, not just the
    # trivial ones, so we cannot honestly claim a clean "trivial slice" saving on
    # the full billable (which is dominated by re-fed cache_read). We claim only
    # the unambiguous part: the model-rate delta on NON-CACHED input+output of the
    # trivial-turn share (output<300). Cache tokens are excluded. Labeled UPPER
    # BOUND / low confidence (per validation: the lever is coarse + quality trade).
    io_save = 0.0
    trivial_evi = []
    for m, b in by_model.items():
        if family(m) not in EXPENSIVE or family(m) not in rates:
            continue
        msgs = b.get("msgs", 0)
        tiny = b.get("tiny_msgs", 0)
        if msgs <= 0 or tiny <= 0:
            continue
        share = tiny / msgs
        rf = rates[family(m)]
        d_in = max(0.0, rf["in"] - sonnet["in"])
        d_out = max(0.0, rf["out"] - sonnet["out"])
        s = share * (b.get("input_tokens", 0) / 1e6 * d_in + b.get("output_tokens", 0) / 1e6 * d_out)
        if s > 0:
            io_save += s
            trivial_evi.append("%s: %d/%d turns (%.0f%%) output<300tok" % (m.split('claude-')[-1], tiny, msgs, 100 * share))
    if io_save >= MIN_FINDING_USD:
        findings.append({
            "signal": "Expensive default model on trivial/short turns",
            "knob": "model (default model per project) / opusplan",
            "config_location": '.claude/settings.json -> {"model": "sonnet"} or {"model": "opusplan"}',
            "recommended_value": "sonnet or opusplan (reserve Opus for hard reasoning)",
            "est_saving_usd": round(io_save, 2),
            "confidence": "low",
            "evidence": "UPPER BOUND. Default model is coarse (affects all turns + trades quality on hard turns); claimed saving = model-rate delta on NON-CACHED input+output of the trivial-turn share ONLY (cache_read/creation excluded). " + "; ".join(trivial_evi),
        })

    drate = dominant_rate(by_model, rates)

    # R2: low cache-hit ratio -> stabilize the cached prefix
    hit = p.get("cache_hit_ratio", 0)
    cc_tot = p.get("cache_creation_input_tokens", 0)
    if hit < 0.85 and cc_tot > 0:
        recoverable = cc_tot * min(1.0, (TARGET_CACHE_HIT - hit) / TARGET_CACHE_HIT)
        s = recoverable / 1e6 * (drate["cw5"] - drate["crd"])
        if s >= MIN_FINDING_USD:
            findings.append({
                "signal": "Low prompt-cache hit ratio",
                "knob": "CLAUDE_CODE_ATTRIBUTION_HEADER=0 + DISABLE_AUTOUPDATER",
                "config_location": '.claude/settings.json -> {"env": {"CLAUDE_CODE_ATTRIBUTION_HEADER": "0", "DISABLE_AUTOUPDATER": "1"}}',
                "recommended_value": "stabilize system-prompt prefix; pin CC version per session",
                "est_saving_usd": round(s, 2),
                "confidence": "medium",
                "evidence": "cache_hit=%.0f%% (<85%%). Recoverable cache-creation ~%d tok re-priced from cache-write to cache-read at dominant model. Estimate; assumes instability is prefix-driven." % (100 * hit, int(recoverable)),
            })

    # R3: 1h-cache write overuse -> FORCE_PROMPT_CACHING_5M (conditional)
    eph1 = p.get("ephemeral_1h_input_tokens", 0)
    if eph1 > 0:
        s = eph1 / 1e6 * (drate["cw1"] - drate["cw5"])
        if s >= 10.0:
            findings.append({
                "signal": "1h-cache write overuse",
                "knob": "FORCE_PROMPT_CACHING_5M",
                "config_location": '.claude/settings.json -> {"env": {"FORCE_PROMPT_CACHING_5M": "1"}}',
                "recommended_value": "force 5m TTL IF sessions are short/bursty (few reads per hour)",
                "est_saving_usd": round(s, 2),
                "confidence": "low",
                "evidence": "ephemeral_1h cache-write=%d tok; delta(1h-5m write rate) at dominant model. UPPER BOUND: only realized if 1h writes are not amortized by long-pause reads." % eph1,
            })

    # R4: MCP / context bloat -> defer tool schemas + prune unused servers
    base_med = p.get("base_context_median", 0)
    nmcp = p.get("distinct_mcp_servers", 0)
    amsgs = p.get("assistant_msgs", 0)
    if base_med > 30000 and nmcp >= 3 and amsgs > 0:
        reducible = max(0, base_med - LEAN_BASE_CONTEXT)
        s = reducible * amsgs / 1e6 * drate["crd"]
        if s >= MIN_FINDING_USD:
            top_srv = list(p.get("mcp_calls", {}).keys())[:6]
            findings.append({
                "signal": "MCP tool-schema / context bloat",
                "knob": "ENABLE_TOOL_SEARCH + prune unused MCP servers",
                "config_location": '.claude/settings.json -> {"env": {"ENABLE_TOOL_SEARCH": "auto:3"}} ; remove unused: claude mcp remove <name> / deniedMcpServers',
                "recommended_value": "defer tool schemas; audit servers seen: " + ", ".join(top_srv),
                "est_saving_usd": round(s, 2),
                "confidence": "medium",
                "evidence": "base_context_median=%d tok across %d MCP servers, re-read over %d assistant turns at dominant cache-read rate (reducible above %dk lean baseline). Lower-bound (cache-read portion only)." % (base_med, nmcp, amsgs, LEAN_BASE_CONTEXT // 1000),
            })

    return findings


def main():
    args = sys.argv[1:]
    pos = [a for a in args if not a.startswith("--")]
    if len(pos) < 3:
        print("usage: recommend.py <raw-stats.json> <knowledge-base.json> <out.json> [--floor USD] [--top-n N]", file=sys.stderr)
        sys.exit(2)
    raw = json.load(open(pos[0]))
    kb = json.load(open(pos[1]))
    out_path = pos[2]
    floor = 30.0
    top_n = 5
    if "--floor" in args:
        floor = float(args[args.index("--floor") + 1])
    if "--top-n" in args:
        top_n = int(args[args.index("--top-n") + 1])

    rates = build_rates((kb.get("pricing") or {}).get("models") or [])

    # estimate $ per project (same method as cost.py) for tiering
    proj_usd = {}
    for name, p in raw["projects"].items():
        usd = 0.0
        for m, b in p.get("by_model", {}).items():
            if family(m) in rates:
                usd += comp_cost(comp_of(b), rates[family(m)])
        proj_usd[name] = usd
    ranked = sorted(proj_usd, key=lambda n: -proj_usd[n])
    tier_a = set(ranked[:top_n])

    out_projects = {}
    by_signal = {}
    total_save = 0.0
    for name, p in raw["projects"].items():
        f = project_findings(p, rates)
        usd = proj_usd[name]
        tier = "A-strong" if name in tier_a else ("B-cheap" if usd >= floor else "C-skip")
        save = round(sum(x["est_saving_usd"] for x in f), 2)
        for x in f:
            by_signal[x["signal"]] = round(by_signal.get(x["signal"], 0) + x["est_saving_usd"], 2)
        total_save += save
        out_projects[name] = {
            "estimated_usd": round(usd, 2),
            "estimated_savings_usd": save,
            "tier": tier,
            "sessions": p.get("sessions", 0),
            "cache_hit_ratio": p.get("cache_hit_ratio", 0),
            "distinct_mcp_servers": p.get("distinct_mcp_servers", 0),
            "web_search_requests": p.get("web_search_requests", 0),
            "web_fetch_requests": p.get("web_fetch_requests", 0),
            "findings": f,
        }

    result = {
        "generated_at": datetime.date.today().isoformat(),
        "engine": "deterministic rules over fresh-docs KB; model-token cost = 0",
        "pricing_provenance": (kb.get("pricing") or {}).get("sources") or kb.get("sources"),
        "assumptions": [
            "Savings are estimates from real token components retariffed at KB pricing; they are not a bill.",
            "opus-4-7 priced at opus-4-8 rates (closest current Opus row).",
            "R1b/R2/R4 use share/ratio assumptions noted per-finding; R1a/R3 are exact token deltas.",
            "Only findings >= $%.0f and mapping to an applyable KB knob are emitted; advisory tips excluded." % MIN_FINDING_USD,
        ],
        "global": {
            "estimated_usd_total": round(sum(proj_usd.values()), 2),
            "estimated_savings_usd_total": round(total_save, 2),
            "by_signal_usd": dict(sorted(by_signal.items(), key=lambda kv: -kv[1])),
            "tier_counts": {
                "A-strong": sum(1 for v in out_projects.values() if v["tier"] == "A-strong"),
                "B-cheap": sum(1 for v in out_projects.values() if v["tier"] == "B-cheap"),
                "C-skip": sum(1 for v in out_projects.values() if v["tier"] == "C-skip"),
            },
        },
        "projects": out_projects,
    }
    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False, sort_keys=True)

    g = result["global"]
    print("global est spend=$%.0f  est savings=$%.0f (%.0f%%)" % (
        g["estimated_usd_total"], g["estimated_savings_usd_total"],
        100 * g["estimated_savings_usd_total"] / g["estimated_usd_total"] if g["estimated_usd_total"] else 0))
    print("savings by signal:")
    for s, v in g["by_signal_usd"].items():
        print("  $%-9.0f %s" % (v, s))
    print("tiers:", g["tier_counts"])
    print("\nTOP projects by est savings:")
    for name, v in sorted(out_projects.items(), key=lambda kv: -kv[1]["estimated_savings_usd"])[:8]:
        print("  $%-8.0f save  ($%-8.0f spend, %d findings) %s" % (
            v["estimated_savings_usd"], v["estimated_usd"], len(v["findings"]), name[-42:]))
    print("wrote", out_path)


if __name__ == "__main__":
    main()
