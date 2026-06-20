#!/usr/bin/env python3
"""
cc-optimizer v2 — inventory: cross "exposed" config against "actually used".

For every project (one session slug = one project root, resolved from the real
cwd in its transcripts), this builds:

  USED   (parsed from sessions/, by REAL tool_use / Skill invocations):
    - mcp server calls            mcp__<server>__tool          -> server
    - claude.ai connector calls   mcp__claude_ai_<C>__...       -> connector
    - plugin MCP calls            mcp__plugin_<plug>_<srv>__... -> plugin
    - skill invocations           Skill(skill="<plug>:<name>")  -> plugin

  EXPOSED (from ~/.claude.json + ~/.claude/settings.json + per-project settings):
    - claude.ai connectors (unless project sets disableClaudeAiConnectors:true)
    - installed plugins (global enabledPlugins, *@*:true = all, minus per-proj false)
    - global user mcpServers (~/.claude.json top-level — leak into EVERY project)

  RECOMMEND (delta = exposed but unused), using ONLY mechanisms proven in prod:
    - connectors 0-use   -> settings.json   "disableClaudeAiConnectors": true
    - plugin 0-use        -> settings.local.json enabledPlugins["<id>"]=false
    - dead key            -> remove no-op "disabledMcpServers"
    - global user-MCP used in <=1 project -> scope-move to owner project (global)

The plugin->capability map is DERIVED from observed usage (a plugin "provides MCP"
if any project ever called plugin_<short>_*, "provides skills" if any Skill named
<short>:*). Pure structural plugins (LSP, hook-only) emit nothing observable and
are therefore never disable-candidates. A small KEEPLIST further protects
framework plugins from bare-name skill ambiguity.

  python3 inventory.py <sessions_dir> <out.json>     # writes report; prints summary
"""
import sys, os, json, glob, re
from collections import Counter, defaultdict


def canon_root(root):
    """Collapse a git-worktree dir onto its parent project. Worktrees live at
    <parent>/.claude/worktrees/<name> and inherit the parent's settings.local.json
    (settings resolution walks UP the tree), so their usage MUST aggregate into the
    parent — else we'd disable a plugin the parent uses only via worktrees."""
    if not root:
        return root
    return re.sub(r"/\.claude/worktrees/[^/]+/?.*$", "", root)

HOME = os.path.realpath(os.path.expanduser("~"))
CLAUDE_JSON = os.path.expanduser("~/.claude.json")
GLOBAL_SETTINGS = os.path.expanduser("~/.claude/settings.json")
PLUGINS_DIR = os.path.expanduser("~/.claude/plugins")
INSTALLED = os.path.join(PLUGINS_DIR, "installed_plugins.json")
CACHE = os.path.join(PLUGINS_DIR, "cache")
MARKETS = os.path.join(PLUGINS_DIR, "marketplaces")

# Framework / structural plugins we never auto-disable (used everywhere, or work
# via hooks/LSP with no observable tool_use, or emit bare-name skills).
KEEPLIST = {"superpowers", "remember", "headroom",
            "typescript-lsp", "pyright-lsp", "dev-squad"}


def load_json(p, default=None):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except Exception:
        return default


def resolve_cwd(sessions_dir, slug, sample_cap=5000):
    """Canonical project ROOT from transcript cwds (dominant cluster -> root-most).
    Robust to subdir/worktree/foreign leakage. Rejects $HOME and '/'."""
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
    cands = {p: c for p, c in cnt.items()
             if os.path.realpath(p) not in (HOME, "/") and p not in ("/", HOME)}
    if not cands:
        return (None, "only $HOME/root cwds found")
    top = max(cands.values())
    strong = [p for p, c in cands.items() if c >= 0.5 * top]
    strong.sort(key=lambda p: (p.rstrip(os.sep).count(os.sep), -cands[p]))
    return (strong[0], "ok")


def parse_usage(folder):
    """Per-project usage from real tool_use/Skill blocks.
    Returns dict: mcp_servers{srv:n}, connectors{c:n}, plugins{short:n}, skills{name:n}."""
    mcp = Counter()        # raw server token after mcp__
    conn = Counter()       # connector display name
    plug = Counter()       # plugin shortname (from plugin MCP calls + skill prefixes)
    skills = Counter()
    for fp in glob.glob(os.path.join(folder, "*.jsonl")):
        try:
            f = open(fp, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                if '"tool_use"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                msg = o.get("message") or {}
                if not isinstance(msg, dict):
                    continue
                c = msg.get("content")
                if not isinstance(c, list):
                    continue
                for b in c:
                    if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                        continue
                    name = b.get("name", "") or ""
                    if name == "Skill":
                        inp = b.get("input") or {}
                        s = str(inp.get("skill") or inp.get("name") or "").strip()
                        if not s:
                            continue
                        s = s.lstrip("/")
                        skills[s] += 1
                        if ":" in s:
                            plug[s.split(":", 1)[0]] += 1
                    elif name.startswith("mcp__"):
                        parts = name.split("__")
                        if len(parts) < 2:
                            continue
                        srv = parts[1]
                        mcp[srv] += 1
                        if srv.startswith("claude_ai_"):
                            conn[srv[len("claude_ai_"):]] += 1
                        elif srv.startswith("plugin_"):
                            # plugin_<short>_<server> -> recover <short> greedily:
                            # short can contain underscores-as-dashes; match against
                            # the longest installed-plugin prefix at resolve time.
                            plug["__RAW__" + srv] += 1
    return {"mcp": dict(mcp), "connectors": dict(conn),
            "plugins": dict(plug), "skills": dict(skills)}


def plugin_short(pid):
    return pid.split("@", 1)[0]


def _ancestors_shallow_to_deep(root):
    """Filesystem ancestors from '/' down to root (inclusive). Claude Code resolves
    settings by walking UP the tree, so a nested project INHERITS every ancestor's
    .claude/settings(.local).json — we must mirror that to avoid redundant edits."""
    root = os.path.abspath(root)
    parts = root.split(os.sep)
    out = []
    for i in range(1, len(parts) + 1):
        out.append(os.sep.join(parts[:i]) or os.sep)
    return out


def inherited_state(root):
    """Effective per-project state from the whole ancestor chain (deeper overrides):
       eff_plugins: {plugin_id: bool}   (explicit enable/disable seen along the path)
       conn_off:    bool                 (disableClaudeAiConnectors, any-source-true)
    Excludes the global ~/.claude/settings.json (handled separately as the base)."""
    home_claude = os.path.realpath(os.path.expanduser("~/.claude"))
    eff_plugins = {}
    conn_off = False
    for d in _ancestors_shallow_to_deep(root):
        cdir = os.path.join(d, ".claude")
        if os.path.realpath(cdir) == home_claude:
            continue  # base layer, not a project override
        for fn in ("settings.json", "settings.local.json"):
            src = load_json(os.path.join(cdir, fn), {}) or {}
            for pid, val in (src.get("enabledPlugins") or {}).items():
                eff_plugins[pid] = val
            if src.get("disableClaudeAiConnectors"):
                conn_off = True
    return eff_plugins, conn_off


def _payload_dir(marketplace, short):
    """Best on-disk payload dir for a plugin: prefer the `.in_use` cache version,
    else the highest version dir, else the marketplace source tree."""
    base = os.path.join(CACHE, marketplace, short)
    if os.path.isdir(base):
        versions = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        in_use = [v for v in versions if os.path.exists(os.path.join(base, v, ".in_use"))]
        pick = (sorted(in_use) or sorted(versions, reverse=True))
        if pick:
            return os.path.join(base, pick[0])
    src = os.path.join(MARKETS, marketplace, "plugins", short)
    return src if os.path.isdir(src) else None


def plugin_catalog(installed_ids):
    """Static capability map per installed plugin, read from its payload on disk.
    Returns short -> {mcp:bool, mcp_servers:[...], skills:int, payload:path}.
    Authoritative (catches skills/MCP that exist but were never invoked)."""
    cat = {}
    for pid in installed_ids:
        short = plugin_short(pid)
        mp = pid.split("@", 1)[1] if "@" in pid else "claude-plugins-official"
        d = _payload_dir(mp, short)
        info = {"mcp": False, "mcp_servers": [], "skills": 0, "payload": d}
        if d:
            man = load_json(os.path.join(d, ".claude-plugin", "plugin.json"), {}) or {}
            servers = man.get("mcpServers") or {}
            if isinstance(servers, dict) and servers:
                info["mcp"] = True
                info["mcp_servers"] = sorted(servers.keys())
            if os.path.exists(os.path.join(d, ".mcp.json")):
                info["mcp"] = True
                dm = load_json(os.path.join(d, ".mcp.json"), {}) or {}
                info["mcp_servers"] = sorted(set(info["mcp_servers"])
                                             | set((dm.get("mcpServers") or {}).keys()))
            sk = os.path.join(d, "skills")
            if os.path.isdir(sk):
                info["skills"] = sum(1 for s in os.listdir(sk)
                                     if os.path.isdir(os.path.join(sk, s)))
        cat[short] = info
    return cat


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    sessions_dir = sys.argv[1]
    out_path = sys.argv[2]

    claude = load_json(CLAUDE_JSON, {}) or {}
    gsettings = load_json(GLOBAL_SETTINGS, {}) or {}
    installed = (load_json(INSTALLED, {}) or {}).get("plugins", [])
    installed_short = {plugin_short(p): p for p in installed}  # short -> full id
    catalog = plugin_catalog(installed)                        # static capabilities
    provides_mcp = {s for s, i in catalog.items() if i["mcp"]}
    provides_skill = {s for s, i in catalog.items() if i["skills"] > 0}
    observable = provides_mcp | provides_skill

    global_mcp = claude.get("mcpServers", {}) or {}             # user-scope leak
    connectors_exist = bool(claude.get("claudeAiMcpEverConnected"))
    gep = gsettings.get("enabledPlugins", {}) or {}
    wildcard_all = gep.get("*@*", False)

    slugs = sorted(d for d in os.listdir(sessions_dir)
                   if os.path.isdir(os.path.join(sessions_dir, d)))

    # ---- pass 1: usage per slug + resolve & canonicalize root (worktrees -> parent) ----
    raw = {}
    slug_root = {}
    for slug in slugs:
        raw[slug] = parse_usage(os.path.join(sessions_dir, slug))
        root, why = resolve_cwd(sessions_dir, slug)
        slug_root[slug] = (canon_root(root), why, root)

    # group slugs by canonical parent root; aggregate usage
    def _new_group():
        return {"slugs": [], "u": {"mcp": Counter(), "connectors": Counter(),
                                   "plugins": Counter(), "skills": Counter()},
                "reason": "ok", "raw_roots": set()}
    groups = defaultdict(_new_group)
    no_root_slugs = []
    for slug in slugs:
        croot, why, root = slug_root[slug]
        if not croot:
            no_root_slugs.append((slug, why))
            continue
        g = groups[croot]
        g["slugs"].append(slug)
        g["raw_roots"].add(root)
        for kind in ("mcp", "connectors", "plugins", "skills"):
            for k, v in raw[slug][kind].items():
                g["u"][kind][k] += v

    def plugin_usage_in(u, short):
        """Total observed use of plugin `short` in one project's usage dict."""
        n = u["plugins"].get(short, 0)
        # plugin MCP calls captured as __RAW__plugin_...
        tok = "plugin_" + short.replace("@", "_")
        for srv, c in u["mcp"].items():
            if srv.startswith(tok + "_") or srv == tok:
                n += c
        for s, c in u["skills"].items():
            if s.split(":", 1)[0] == short:
                n += c
        return n

    # ---- pass 2: exposed vs used -> recommendations per CANONICAL project ----
    projects = []
    server_project_use = defaultdict(set)   # global user server -> set(root with use)
    for root in sorted(groups):
        g = groups[root]
        u = g["u"]
        member_slugs = g["slugs"]
        # representative slug (kept for back-compat / display)
        slug = max(member_slugs, key=lambda s: sum(raw[s]["mcp"].values())
                   + sum(raw[s]["skills"].values()), default=member_slugs[0])
        rec = {"slug": slug, "root": root, "root_reason": "ok",
               "member_slugs": member_slugs, "n_worktrees": len(member_slugs) - 1,
               "recommendations": [], "kept": [], "no_root": False}

        # per-project settings: OWN files (dead-key removal target) + INHERITED state
        proj_settings = load_json(os.path.join(root, ".claude", "settings.json"), {}) or {}
        proj_local = load_json(os.path.join(root, ".claude", "settings.local.json"), {}) or {}
        # effective enabledPlugins / connector state across the whole ancestor chain
        # (a nested project inherits its parent's disables — don't re-recommend them).
        ep_local, already_no_connectors = inherited_state(root)
        rec["inherited_from_parent"] = bool(
            ep_local and not (proj_settings.get("enabledPlugins") or proj_local.get("enabledPlugins")))
        has_dead_key = ("disabledMcpServers" in proj_settings
                        or "disabledMcpServers" in proj_local)

        # (a) connectors
        conn_used = sum(u["connectors"].values())
        if connectors_exist and not already_no_connectors:
            if conn_used == 0:
                rec["recommendations"].append({
                    "kind": "disable_connectors", "target": "settings.json",
                    "key": "disableClaudeAiConnectors", "value": True,
                    "evidence": "0 claude.ai connector calls in this project"})
            else:
                rec["kept"].append("claude.ai connectors (%d calls)" % conn_used)

        # (b) plugins exposed but unused
        for short, pid in installed_short.items():
            if short in KEEPLIST or short not in observable:
                continue
            # exposed to this project?
            project_false = ep_local.get(pid) is False or ep_local.get(short) is False
            globally_enabled = wildcard_all or gep.get(pid) is True
            if project_false or not globally_enabled:
                continue
            used = plugin_usage_in(u, short)
            if used == 0:
                ci = catalog.get(short, {})
                prov = []
                if ci.get("mcp"):
                    prov.append("MCP(%s)" % ",".join(ci.get("mcp_servers") or ["?"]))
                if ci.get("skills"):
                    prov.append("%d skills" % ci["skills"])
                rec["recommendations"].append({
                    "kind": "disable_plugin", "target": "settings.local.json",
                    "key": "enabledPlugins.%s" % pid, "value": False,
                    "evidence": "plugin provides %s; 0 use in this project"
                                % (", ".join(prov) or "MCP/skills")})
            else:
                rec["kept"].append("%s (%d use)" % (short, used))

        # (c) dead key
        if has_dead_key:
            rec["recommendations"].append({
                "kind": "remove_dead_key", "target": "settings(.local).json",
                "key": "disabledMcpServers", "value": None,
                "evidence": "unsupported key, silently ignored (no-op)"})

        # tally global server usage for scope-move pass
        for srv, c in u["mcp"].items():
            if srv in global_mcp:
                server_project_use[srv].add(root)

        projects.append(rec)

    # carry the no-root slugs through as informational skips
    for slug, why in no_root_slugs:
        projects.append({"slug": slug, "root": None, "root_reason": why,
                         "member_slugs": [slug], "n_worktrees": 0,
                         "recommendations": [], "kept": [], "no_root": True})

    # ---- pass 3: scope-move for leaking global user MCP servers ----
    scope_moves = []
    for srv, defn in global_mcp.items():
        users = sorted(server_project_use.get(srv, set()))
        owner = None
        # prefer a path referenced in the server's args
        for a in (defn.get("args") or []):
            if isinstance(a, str) and a.startswith("/Users/"):
                cand = a
                # climb to a sources/<proj> root if present
                owner = cand
                break
        scope_moves.append({
            "server": srv, "used_in_projects": users, "n_user_projects": len(users),
            "owner_hint": owner, "definition": defn,
            "recommend_move": len(users) <= 1,
            "evidence": "global user MCP leaks into ALL projects; used in %d" % len(users)})

    report = {
        "sessions_dir": sessions_dir,
        "n_projects": len(projects),
        "capabilities": {"provides_mcp": sorted(provides_mcp),
                         "provides_skill": sorted(provides_skill),
                         "skill_counts": {s: catalog[s]["skills"]
                                          for s in sorted(catalog) if catalog[s]["skills"]},
                         "keeplist": sorted(KEEPLIST)},
        "global_user_mcpServers": sorted(global_mcp.keys()),
        "connectors_exist": connectors_exist,
        "projects": projects,
        "scope_moves": scope_moves,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # ---- summary ----
    n_rec = sum(len(p["recommendations"]) for p in projects)
    by_kind = Counter(r["kind"] for p in projects for r in p["recommendations"])
    noroot = sum(1 for p in projects if p["no_root"])
    print("=== cc-optimizer v2 inventory ===")
    print("projects: %d  (%d without resolvable root)" % (len(projects), noroot))
    print("observable plugins: mcp=%s  skill=%s"
          % (sorted(provides_mcp), sorted(provides_skill)))
    print("recommendations: %d total" % n_rec)
    for k, c in by_kind.most_common():
        print("   %-18s %d" % (k, c))
    movers = [s for s in scope_moves if s["recommend_move"]]
    print("scope-moves (global user MCP, used in <=1 proj): %d" % len(movers))
    for s in movers:
        print("   %s  used_in=%s  owner_hint=%s"
              % (s["server"], s["used_in_projects"], s["owner_hint"]))
    print("\nreport -> %s" % out_path)


if __name__ == "__main__":
    main()
