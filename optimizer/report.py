#!/usr/bin/env python3
"""
cc-optimizer - human-facing report generator.

Renders findings.json (the structured source of truth) into a Markdown
levantamento. Pure rendering: no model tokens, no recomputation. Works on
the deterministic findings or on the LLM-refined findings (same schema).

  python3 report.py <findings.json> <raw-stats.json> <knowledge-base.json> <out.md>
"""
import sys, json


def disp(slug):
    s = slug
    if "sources-" in s:
        s = s.split("sources-")[-1]
    else:
        s = s.strip("-").split("-")[-1]
    return s.replace("--claude-worktrees-", " (worktree:") + ("" if "worktree" not in s else ")")


def main():
    findings = json.load(open(sys.argv[1]))
    raw = json.load(open(sys.argv[2]))
    kb = json.load(open(sys.argv[3]))
    out = sys.argv[4]

    g = findings["global"]
    rg = raw["global"]
    L = []
    w = L.append

    w("# Claude Code — Levantamento de Desperdício de Cota")
    w("")
    w("> Gerado em **%s** · motor: %s" % (findings.get("generated_at", "?"), findings.get("engine", "")))
    w("> Pricing/knobs de **docs oficiais frescas** (não de memória). Escopo: **levantamento** — nada é aplicado automaticamente; cada achado é uma *proposta*.")
    w("")
    w("## Sumário executivo")
    w("")
    w("| Métrica | Valor |")
    w("|---|---|")
    w("| Sessões analisadas | %s |" % f"{raw['files_scanned']:,}")
    w("| Projetos | %d |" % rg.get("projects", len(raw["projects"])))
    w("| Tokens totais (in+out+cache) | %s |" % f"{rg['total_tokens']:,}")
    w("| Cache hit global | %.0f%% |" % (rg.get("cache_hit_ratio", 0) * 100))
    w("| **Gasto histórico modelado** | **$%s** |" % f"{g['estimated_usd_total']:,.0f}")
    det = g.get("estimated_savings_usd_deterministic")
    spend = g["estimated_usd_total"] or 1
    if det and abs(det - g["estimated_savings_usd_total"]) > 1:
        w("| Economia — teto determinístico | $%s (%.0f%%) |" % (f"{det:,.0f}", 100 * det / spend))
        w("| **Economia validada (recomendada)** | **$%s (%.0f%%)** |" % (
            f"{g['estimated_savings_usd_total']:,.0f}", 100 * g["estimated_savings_usd_total"] / spend))
    else:
        w("| **Economia acionável estimada** | **$%s (%.0f%%)** |" % (
            f"{g['estimated_savings_usd_total']:,.0f}", 100 * g["estimated_savings_usd_total"] / spend))
    w("")
    # model mix
    bm = rg.get("by_model_tokens", {})
    tot = sum(bm.values()) or 1
    w("**Mix de modelos (tokens):** " + " · ".join(
        "%s %.0f%%" % (m.replace("claude-", ""), 100 * t / tot)
        for m, t in sorted(bm.items(), key=lambda kv: -kv[1])[:5]))
    w("")
    w("### Economia por alavanca")
    w("")
    w("| Alavanca (sinal) | Economia estimada |")
    w("|---|---|")
    for s, v in g.get("by_signal_usd", {}).items():
        w("| %s | $%s |" % (s, f"{v:,.0f}"))
    w("")

    tiers = g.get("tier_counts", {})
    w("Roteamento de projetos: **%d** alta prioridade · **%d** média · **%d** abaixo do limiar (aplicar defaults globais)."
      % (tiers.get("A-strong", 0), tiers.get("B-cheap", 0), tiers.get("C-skip", 0)))
    w("")

    # rollup (LLM cross-project synthesis) if present
    rollup = g.get("rollup") or {}
    if rollup.get("executive_summary"):
        w("## Síntese executiva")
        w("")
        if det and abs(det - g["estimated_savings_usd_total"]) > 1:
            w("> Nota de reconciliação: a síntese abaixo cita o **teto determinístico (~$%s)**; a validação LLM conservadora (descartou achados não-aplicáveis e nunca elevou estimativas) reduziu para **$%s — o número recomendado** no topo." % (f"{det:,.0f}", f"{g['estimated_savings_usd_total']:,.0f}"))
            w("")
        w(rollup["executive_summary"])
        w("")
    w("## Ação global recomendada (nível usuário)")
    w("")
    ga = rollup.get("global_actions")
    if ga:
        w("| Ação | Config (onde) | Aplica a | Por quê |")
        w("|---|---|---|---|")
        for a in ga:
            w("| %s | `%s` | %s | %s |" % (
                a.get("action", ""), a.get("config_location", ""),
                a.get("applies_to", "todos"), a.get("rationale", "")))
        w("")
    else:
        w("Como Opus domina o uso em quase todos os projetos, os maiores ganhos vêm de defaults no `~/.claude/settings.json`:")
        w("")
        w("```jsonc")
        w("{")
        w('  "env": {')
        w('    "CLAUDE_CODE_SUBAGENT_MODEL": "haiku",   // subagentes mecânicos no modelo barato')
        w('    "MAX_MCP_OUTPUT_TOKENS": "10000"         // teto de saída de tools MCP')
        w("  },")
        w('  "effortLevel": "medium"                    // menos tokens de raciocínio por turno')
        w("}")
        w("```")
        w("")
    for pat in rollup.get("cross_project_patterns", []):
        w("- %s" % pat)
    if rollup.get("cross_project_patterns"):
        w("")

    # per-project
    projects = findings["projects"]
    ranked = sorted(projects.items(), key=lambda kv: -kv[1]["estimated_savings_usd"])

    def render_project(name, p):
        w("### %s" % disp(name))
        w("")
        w("`%s`" % name)
        w("")
        w("Gasto modelado **$%s** · economia estimada **$%s** · %d sessões · cache hit %.0f%% · %d MCP servers"
          % (f"{p['estimated_usd']:,.0f}", f"{p['estimated_savings_usd']:,.0f}", p.get("sessions", 0),
             p.get("cache_hit_ratio", 0) * 100, p.get("distinct_mcp_servers", 0)))
        w("")
        if p.get("profile"):
            w("_%s_" % p["profile"])
            w("")
        if not p["findings"]:
            w("_Sem achados acima do limiar._")
            w("")
            return
        w("| Sinal | Config (onde) | Valor recomendado | Economia | Conf. |")
        w("|---|---|---|---|---|")
        for x in sorted(p["findings"], key=lambda f: -f["est_saving_usd"]):
            w("| %s | `%s` | %s | $%s | %s |" % (
                x["signal"], x["config_location"], x["recommended_value"],
                f"{x['est_saving_usd']:,.0f}", x.get("confidence", "")))
        w("")
        for x in sorted(p["findings"], key=lambda f: -f["est_saving_usd"]):
            w("- **%s** — _evidência:_ %s" % (x["signal"], x.get("evidence", "")))
        w("")

    w("## Projetos prioritários (alta)")
    w("")
    for name, p in ranked:
        if p["tier"] == "A-strong":
            render_project(name, p)
    w("## Projetos de média prioridade")
    w("")
    for name, p in ranked:
        if p["tier"] == "B-cheap" and p["findings"]:
            render_project(name, p)

    skipped = [(n, p) for n, p in ranked if p["tier"] == "C-skip"]
    skip_spend = sum(p["estimated_usd"] for _, p in skipped)
    w("## Abaixo do limiar de tuning (%d projetos, ~$%s)" % (len(skipped), f"{skip_spend:,.0f}"))
    w("")
    w("Gasto individual baixo demais para tuning por projeto — cobertos pelos defaults globais acima.")
    w("")

    # methodology + assumptions
    w("## Metodologia e premissas")
    w("")
    w("Parse 100%% determinístico dos `.jsonl` (campos `usage`/`model` já gravados) → custo de modelo **zero** na análise. Pricing e knobs vêm de `knowledge-base.json` (docs oficiais).")
    w("")
    for a in findings.get("assumptions", []):
        w("- %s" % a)
    w("")
    for c in (g.get("rollup") or {}).get("caveats", []):
        w("- %s" % c)
    if (g.get("rollup") or {}).get("caveats"):
        w("")

    # sources
    srcs = kb.get("sources", []) or findings.get("pricing_provenance", [])
    if srcs:
        w("## Fontes oficiais")
        w("")
        for s in srcs:
            w("- %s" % s)
        w("")

    # exclusions
    excl = kb.get("excluded_advisory", [])
    if excl:
        w("## Excluído (consultivo/dica — não acionável como config)")
        w("")
        for e in excl:
            w("- %s" % e)
        w("")

    open(out, "w").write("\n".join(L))
    print("wrote", out, "(%d lines)" % len(L))


if __name__ == "__main__":
    main()
