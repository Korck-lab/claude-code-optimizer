#!/usr/bin/env python3
"""
cc-optimizer - apply the actionable findings to per-project settings.json.

SAFE BY DEFAULT: dry-run unless --apply is passed. It only ever AUTO-applies
the unambiguous, low-risk config mutations (model/env keys). Anything
conditional or destructive (forcing 5m cache TTL, removing MCP servers) is
listed under MANUAL and never written automatically.

  python3 apply.py [--apply] [--global] [--project SLUG] [--min-usd N]
                   [--min-confidence low|medium|high]
                   [--findings PATH] [--sessions DIR]

Project directories are resolved from the `cwd` recorded in the transcripts
(slug decoding is ambiguous: the slug "dev-squad" maps to the dir "dev_squad").
Because a project's transcripts contain MANY cwds (subdirs the agent cd'd into,
worktrees, even other projects that leaked in, and sometimes $HOME), we pick the
dominant ROOT cwd (most-common, then root-most), and reject implausible targets
($HOME, /). Writes deep-merge into any existing .claude/settings.json (never
clobbering other keys), save a timestamped .bak, and are idempotent.

Default --min-confidence is "medium": the big lever (subagent model, high conf)
is included; the low-confidence upper-bound levers (e.g. opusplan) are opt-in via
--min-confidence low. Dry-run previews EXACTLY what --apply would write.
"""
import sys, os, json, glob, re, time, shutil
from collections import Counter

CONF_RANK = {"low": 0, "medium": 1, "high": 2}

GLOBAL_DEFAULTS = {
    "env": {
        "CLAUDE_CODE_SUBAGENT_MODEL": "haiku",   # mechanical subagents on the cheap tier
        "MAX_MCP_OUTPUT_TOKENS": "25000",        # cap any single MCP tool response
    },
}


def resolve_cwd(sessions_dir, slug, sample_cap=5000):
    """Pick a project's canonical ROOT directory from the cwd values in its
    transcripts. Robust to subdir/worktree/foreign-project leakage:
    count all cwds, keep the dominant cluster (>=50% of the top count), then
    choose the ROOT-most (shortest path) among it. Reject $HOME and '/'.
    Returns (path|None, reason)."""
    home = os.path.realpath(os.path.expanduser("~"))
    cnt = Counter()
    seen = 0
    for fp in sorted(glob.glob(os.path.join(sessions_dir, slug, "*.jsonl"))):
        if seen >= sample_cap:
            break
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"cwd"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    c = o.get("cwd")
                    if c:
                        cnt[c] += 1
                        seen += 1
                        if seen >= sample_cap:
                            break
        except OSError:
            continue
    if not cnt:
        return (None, "no cwd in transcripts")
    # drop implausible candidates
    cands = {p: c for p, c in cnt.items()
             if os.path.realpath(p) not in (home, "/") and p not in ("/", home)}
    if not cands:
        return (None, "only $HOME/root cwds found")
    top = max(cands.values())
    strong = [p for p, c in cands.items() if c >= 0.5 * top]
    # root-most (fewest path separators), tie -> higher count
    strong.sort(key=lambda p: (p.rstrip(os.sep).count(os.sep), -cands[p]))
    return (strong[0], "ok")


def classify(f):
    """Map a finding -> ('auto', [(keypath, value), ...]) | ('manual', reason) | ('skip', reason)."""
    cl = f.get("config_location", "")
    rv = f.get("recommended_value", "")
    text = (cl + " " + rv).lower()

    if "claude_code_subagent_model" in text:
        val = "haiku" if "haiku" in rv.lower() else ("sonnet" if "sonnet" in rv.lower() else "haiku")
        return ("auto", [(["env", "CLAUDE_CODE_SUBAGENT_MODEL"], val)])

    if "force_prompt_caching_5m" in text:
        if any(k in rv.lower() for k in ("do not", "do-not", "leave 1h", "not set")):
            return ("skip", "keep 1h caching (refiner said not to force 5m here)")
        # FORCE recommendation: only fires on short/bursty-session projects, where
        # forcing the cheaper 5m TTL is correct. Reversible env var.
        return ("auto", [(["env", "FORCE_PROMPT_CACHING_5M"], "1")])

    if "enable_tool_search" in text:
        m = re.search(r"auto:\d+", text)
        muts = [(["env", "ENABLE_TOOL_SEARCH"], m.group(0) if m else "auto:3")]
        if "max_mcp_output_tokens" in text:
            mo = re.search(r"max_mcp_output_tokens['\"\s:=]+(\d+)", text)
            muts.append((["env", "MAX_MCP_OUTPUT_TOKENS"], mo.group(1) if mo else "25000"))
        return ("auto", muts)

    if "max_mcp_output_tokens" in text and "enable_tool_search" not in text:
        mo = re.search(r"max_mcp_output_tokens['\"\s:=]+(\d+)", text)
        return ("auto", [(["env", "MAX_MCP_OUTPUT_TOKENS"], mo.group(1) if mo else "25000")])

    if "attribution_header" in text or "disable_autoupdater" in text:
        muts = []
        if "attribution_header" in text:
            muts.append((["env", "CLAUDE_CODE_ATTRIBUTION_HEADER"], "0"))
        if "disable_autoupdater" in text:
            muts.append((["env", "DISABLE_AUTOUPDATER"], "1"))
        return ("auto", muts)

    if "mcp remove" in text or "deniedmcpservers" in text or "claude mcp" in text or "remove " in rv.lower():
        return ("manual", "MCP server prune — review .mcp.json server list: " + rv)

    if "opusplan" in text or ('"model"' in cl and "subagent" not in text):
        if any(k in rv.lower() for k in ("do not", "do-not", "leave")):
            return ("skip", "keep current default model")
        val = "opusplan" if "opusplan" in text else ("sonnet" if "sonnet" in rv.lower() else None)
        if val:
            return ("auto", [(["model"], val)])
        return ("manual", "default model change — pick a value: " + rv)

    return ("manual", "unmapped knob — review: " + cl)


def set_path(d, keypath, value):
    cur = d
    for k in keypath[:-1]:
        cur = cur.setdefault(k, {})
        if not isinstance(cur, dict):
            return False
    if cur.get(keypath[-1]) == value:
        return False
    cur[keypath[-1]] = value
    return True


def main():
    argv = sys.argv[1:]
    do_apply = "--apply" in argv
    do_global = "--global" in argv
    findings_path = "optimizer/out/findings-final.json"
    sessions_dir = "sessions"
    project_filter = None
    min_usd = 0.0
    min_conf = "medium"
    report_path = None
    excludes = []   # dotted keypaths to skip (e.g. --exclude model)
    only = []       # if set, apply ONLY these dotted keypaths (allowlist)
    for i, a in enumerate(argv):
        if a == "--findings":
            findings_path = argv[i + 1]
        elif a == "--sessions":
            sessions_dir = argv[i + 1]
        elif a == "--project":
            project_filter = argv[i + 1]
        elif a == "--min-usd":
            min_usd = float(argv[i + 1])
        elif a == "--min-confidence":
            min_conf = argv[i + 1]
        elif a == "--report":
            report_path = argv[i + 1]
        elif a == "--exclude":
            excludes.append(argv[i + 1])
        elif a == "--only":
            only.append(argv[i + 1])
    report = []  # before/after audit, written if --report given

    data = json.load(open(findings_path))
    mode = "APPLY (writing files)" if do_apply else "DRY-RUN (no writes)"
    print("=== cc-optimizer apply — %s ===" % mode)
    print("filter: min_confidence>=%s, min_usd>=%g%s\n" % (
        min_conf, min_usd, (", project~%s" % project_filter) if project_filter else ""))

    # resolve every project's root dir once (for both the write loop and the
    # transparency stats), so the user sees how much of the corpus is writable.
    resolved = {}
    res_ok = res_gone = res_nocwd = 0
    for slug in data["projects"]:
        path, reason = resolve_cwd(sessions_dir, slug)
        if path is None:
            resolved[slug] = (None, reason)
            res_nocwd += 1
        elif os.path.isdir(path):
            resolved[slug] = (path, "ok")
            res_ok += 1
        else:
            resolved[slug] = (None, "dir gone: " + path)
            res_gone += 1

    manual = []
    skipped = []
    changed_projects = 0
    total_mutations = 0
    total_usd = 0.0

    for slug, proj in data["projects"].items():
        if project_filter and project_filter not in slug:
            continue
        findings = [f for f in proj.get("findings", [])
                    if f.get("est_saving_usd", 0) >= min_usd
                    and CONF_RANK.get(f.get("confidence", "low"), 0) >= CONF_RANK.get(min_conf, 1)]
        auto_muts = []  # (keypath, value, conf, usd, signal)
        for f in proj.get("findings", []):
            kind = classify(f)
            if kind[0] == "manual":
                manual.append((proj.get("name", slug), f.get("signal", ""), kind[1]))
        for f in findings:
            kind = classify(f)
            if kind[0] == "auto":
                for kp, val in kind[1]:
                    auto_muts.append((kp, val, f.get("confidence", "low"), f.get("est_saving_usd", 0), f.get("signal", "")))
        if not auto_muts:
            continue

        path, reason = resolved.get(slug, (None, "unknown"))
        if path is None:
            skipped.append((slug, reason))
            continue

        settings_path = os.path.join(path, ".claude", "settings.json")
        settings = {}
        existed = os.path.isfile(settings_path)
        if existed:
            try:
                settings = json.load(open(settings_path))
            except Exception:
                skipped.append((slug, "existing settings.json unparseable"))
                continue
        before_state = json.loads(json.dumps(settings))  # snapshot BEFORE

        applied_here = []
        for kp, val, conf, usd, sig in auto_muts:
            dotted = ".".join(kp)
            if any(dotted == ex or dotted.startswith(ex + ".") for ex in excludes):
                continue
            if only and not any(dotted == o or dotted.startswith(o + ".") for o in only):
                continue
            if set_path(settings, list(kp), val):
                applied_here.append((dotted, val, conf, usd))
        if not applied_here:
            continue

        changed_projects += 1
        total_mutations += len(applied_here)
        print("• %s   ->   %s" % (proj.get("name", slug), settings_path))
        for k, v, conf, usd in applied_here:
            total_usd += usd
            print("    %-34s = %-12s [%s%s]" % (k, json.dumps(v), conf, (", ~$%.0f" % usd) if usd else ""))
        if do_apply:
            os.makedirs(os.path.dirname(settings_path), exist_ok=True)
            if os.path.isfile(settings_path):
                shutil.copy2(settings_path, settings_path + ".bak." + time.strftime("%Y%m%d%H%M%S"))
            with open(settings_path, "w") as out:
                json.dump(settings, out, indent=2)
            print("    -> written (backup saved)")
        report.append({
            "project": slug, "name": proj.get("name", slug),
            "settings_path": settings_path, "existed_before": existed, "written": do_apply,
            "before": before_state, "after": settings,
            "mutations": [{"key": k, "value": v, "confidence": c, "est_saving_usd": u}
                          for k, v, c, u in applied_here],
        })
        print()

    if do_global:
        gpath = os.path.expanduser("~/.claude/settings.json")
        print("=== GLOBAL (user-level) %s ===" % gpath)
        print("  ⚠️  isto altera sua config GLOBAL do Claude Code — revise antes de aplicar.")
        print("  proposed defaults:", json.dumps(GLOBAL_DEFAULTS))
        if do_apply:
            g = {}
            if os.path.isfile(gpath):
                g = json.load(open(gpath))
                shutil.copy2(gpath, gpath + ".bak." + time.strftime("%Y%m%d%H%M%S"))
            for k, v in GLOBAL_DEFAULTS.get("env", {}).items():
                set_path(g, ["env", k], v)
            json.dump(g, open(gpath, "w"), indent=2)
            print("  -> written (backup saved)")
        else:
            print("  (dry-run — not written; override per-project to sonnet on code-heavy projects)")
        print()

    print("=== SUMMARY ===")
    print("directory resolution (all %d projects): %d resolved to existing dir · %d dirs gone · %d no cwd"
          % (len(data["projects"]), res_ok, res_gone, res_nocwd))
    print("auto mutations: %d across %d project settings.json (targeting ~$%.0f of the modeled historical savings)"
          % (total_mutations, changed_projects, total_usd))
    if skipped:
        print("skipped %d project(s) with findings but no writable dir:" % len(skipped))
        for s, why in skipped[:12]:
            print("  - %s (%s)" % (s[-44:], why))
    if manual:
        print("\nMANUAL — review & apply yourself (never auto-applied):")
        seen = set()
        for name, sig, reason in manual:
            key = (name, reason)
            if key in seen:
                continue
            seen.add(key)
            print("  - [%s] %s: %s" % (name, sig, reason))
    if report_path:
        with open(report_path, "w") as rf:
            json.dump({"mode": "apply" if do_apply else "dry-run",
                       "min_confidence": min_conf, "min_usd": min_usd,
                       "resolution": {"resolved": res_ok, "dir_gone": res_gone, "no_cwd": res_nocwd},
                       "projects": report}, rf, indent=2, ensure_ascii=False)
        print("\nbefore/after audit written: %s (%d project records)" % (report_path, len(report)))

    if not do_apply:
        print("\nDRY-RUN only. Re-run with --apply to write per-project (+ --global for user defaults).")
        print("Include the low-confidence upper-bound levers (e.g. opusplan) with: --min-confidence low")


if __name__ == "__main__":
    main()
