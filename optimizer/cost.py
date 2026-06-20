#!/usr/bin/env python3
"""
cc-optimizer - deterministic cost weighting.

Joins raw-stats.json (token aggregates, model-cost-free) with
knowledge-base.json (per-model pricing fetched from FRESH official docs --
never hardcoded here) to estimate $ spend per project, and ranks projects
by estimated spend so the synthesis phase can route the top-N to a stronger
(more expensive) model and the rest to a cheaper one.

  python3 cost.py <raw-stats.json> <knowledge-base.json> <out.json> [top_n]

No network, no model tokens. Pure arithmetic. If a model in the logs has no
pricing row in the KB, its cost is reported as 0 and the model is listed
under `unpriced_models` so the gap is explicit (not silently hidden).
"""
import sys, json


def family(model_id):
    m = (model_id or "").lower()
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if fam in m:
            return fam
    return None


def build_rate_index(models):
    """Map family -> pricing row (newest/most specific wins on ties)."""
    idx = {}
    for row in models:
        fam = family(row.get("model"))
        if fam and fam not in idx:
            idx[fam] = row
    return idx


def rate(row, key):
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def cost_for_model(model_id, b, rate_idx):
    """Estimate USD for one model bucket b using KB rates. Returns (usd, priced)."""
    fam = family(model_id)
    row = rate_idx.get(fam) if fam else None
    if not row:
        return 0.0, False
    inp = rate(row, "input_per_mtok")
    out = rate(row, "output_per_mtok")
    cw5 = rate(row, "cache_write_5m_per_mtok") or inp * 1.25
    cw1 = rate(row, "cache_write_1h_per_mtok") or inp * 2.0
    crd = rate(row, "cache_read_per_mtok") or inp * 0.1
    # non-cached input = input_tokens; cache writes split into 1h/5m; reads cheap
    eph1 = b.get("ephemeral_1h_input_tokens", 0)
    eph5 = b.get("ephemeral_5m_input_tokens", 0)
    cc = b.get("cache_creation_input_tokens", 0)
    # any cache_creation not attributed to 1h/5m falls back to 5m rate
    leftover = max(0, cc - eph1 - eph5)
    usd = (
        b.get("input_tokens", 0) / 1e6 * inp
        + b.get("output_tokens", 0) / 1e6 * out
        + eph1 / 1e6 * cw1
        + (eph5 + leftover) / 1e6 * cw5
        + b.get("cache_read_input_tokens", 0) / 1e6 * crd
    )
    return usd, True


def main():
    if len(sys.argv) < 4:
        print("usage: cost.py <raw-stats.json> <knowledge-base.json> <out.json> [top_n]", file=sys.stderr)
        sys.exit(2)
    raw = json.load(open(sys.argv[1]))
    kb = json.load(open(sys.argv[2]))
    out_path = sys.argv[3]
    top_n = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    pricing = (kb.get("pricing") or {})
    models = pricing.get("models") or []
    rate_idx = build_rate_index(models)
    if not rate_idx:
        print("WARN: no usable pricing rows in knowledge-base.json", file=sys.stderr)

    unpriced = set()
    out_projects = {}
    for name, p in raw.get("projects", {}).items():
        total_usd = 0.0
        by_model_usd = {}
        opus_usd = 0.0
        for model_id, b in p.get("by_model", {}).items():
            usd, priced = cost_for_model(model_id, b, rate_idx)
            if not priced and (b.get("input_tokens", 0) + b.get("output_tokens", 0)) > 0:
                unpriced.add(model_id)
            by_model_usd[model_id] = round(usd, 2)
            total_usd += usd
            if family(model_id) == "opus":
                opus_usd += usd
        out_projects[name] = {
            "estimated_usd": round(total_usd, 2),
            "opus_usd": round(opus_usd, 2),
            "opus_usd_share": round(opus_usd / total_usd, 3) if total_usd else 0.0,
            "by_model_usd": by_model_usd,
            "total_tokens": p.get("total_tokens", 0),
            "sessions": p.get("sessions", 0),
            "cache_hit_ratio": p.get("cache_hit_ratio", 0),
        }

    ranked = sorted(out_projects.items(), key=lambda kv: -kv[1]["estimated_usd"])
    top = [name for name, _ in ranked[:top_n]]
    global_usd = round(sum(v["estimated_usd"] for v in out_projects.values()), 2)

    result = {
        "pricing_source": pricing.get("sources") or kb.get("sources") or [],
        "pricing_ratios": pricing.get("ratios"),
        "global_estimated_usd": global_usd,
        "top_n": top_n,
        "top_projects": top,
        "unpriced_models": sorted(unpriced),
        "projects": out_projects,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)

    print("global_estimated_usd=$%.2f  priced_models=%s  unpriced=%s" % (
        global_usd, sorted(rate_idx.keys()), sorted(unpriced)))
    print("TOP %d projects by estimated $ (-> stronger-model synthesis):" % top_n)
    for name, v in ranked[:top_n]:
        print("  $%-10.2f opus=%2.0f%% tok=%-12d  %s" % (
            v["estimated_usd"], v["opus_usd_share"] * 100, v["total_tokens"], name[-48:]))
    print("... rest (%d projects) -> cheaper-model synthesis" % max(0, len(ranked) - top_n))
    print("wrote", out_path)


if __name__ == "__main__":
    main()
