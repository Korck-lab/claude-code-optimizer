#!/usr/bin/env python3
"""
cc-optimizer v2 — apply: turn inventory recommendations into real per-project
config edits, using ONLY mechanisms validated in production:

  disable_connectors -> <root>/.claude/settings.json        "disableClaudeAiConnectors": true
  disable_plugin      -> <root>/.claude/settings.local.json  enabledPlugins["<id>"] = false (merge)
  remove_dead_key     -> remove the no-op "disabledMcpServers" from settings(.local).json

SAFE BY DEFAULT (dry-run). Writes only with --apply. Per touched file: deep-merge
(never clobbers other keys), timestamped .bak, then on-disk verify (intended
mutations present, zero collateral, .bak exists). The global ~/.claude/settings.json
is NEVER written. Projects without a resolvable root are skipped and reported.

  python3 apply2.py <inventory.json>                 # dry-run plan + per-project report
  python3 apply2.py <inventory.json> --apply         # write all
  python3 apply2.py <inventory.json> --project <slug-substr> [--apply]
  python3 apply2.py <inventory.json> --only disable_connectors,remove_dead_key [--apply]
  python3 apply2.py <inventory.json> --report out/apply2-report.json
"""
import sys, os, json, copy, shutil, subprocess
from collections import Counter


def load(p, d=None):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except Exception:
        return d


def flatten(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, prefix + repr(k) + "."))
    elif isinstance(d, list):
        out[prefix.rstrip(".")] = json.dumps(d, sort_keys=True)
    else:
        out[prefix.rstrip(".")] = d
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    inv = load(sys.argv[1])
    if not inv:
        print("cannot read inventory:", sys.argv[1])
        sys.exit(2)
    args = sys.argv[2:]
    apply = "--apply" in args
    proj_filter = None
    only = None
    report_path = None
    for i, a in enumerate(args):
        if a == "--project" and i + 1 < len(args):
            proj_filter = args[i + 1]
        if a == "--only" and i + 1 < len(args):
            only = set(args[i + 1].split(","))
        if a == "--report" and i + 1 < len(args):
            report_path = args[i + 1]
    TS = subprocess.check_output(["date", "+%Y%m%d%H%M%S"]).decode().strip()

    planned = []      # per-project plan
    skipped = []
    for p in inv["projects"]:
        slug, root = p["slug"], p.get("root")
        recs = [r for r in p["recommendations"] if (only is None or r["kind"] in only)]
        if proj_filter and proj_filter not in slug:
            continue
        if not recs:
            continue
        if not root:
            skipped.append((slug, "no resolvable root"))
            continue
        if "/.claude/worktrees/" in root or "/worktrees/" in root:
            skipped.append((slug, "ephemeral git worktree — skipped"))
            continue
        if not os.path.isdir(root):
            skipped.append((slug, "root dir missing: %s" % root))
            continue
        # group mutations by target file
        f_settings = os.path.join(root, ".claude", "settings.json")
        f_local = os.path.join(root, ".claude", "settings.local.json")
        plan = {"slug": slug, "root": root, "files": {}}

        def ensure(path):
            plan["files"].setdefault(path, {"before": load(path, {}) or {},
                                            "mut": [], "existed": os.path.isfile(path)})
            return plan["files"][path]

        for r in recs:
            k = r["kind"]
            if k == "disable_connectors":
                e = ensure(f_settings)
                e["mut"].append((["disableClaudeAiConnectors"], True, r))
            elif k == "disable_plugin":
                pid = r["key"].split(".", 1)[1]
                e = ensure(f_local)
                e["mut"].append((["enabledPlugins", pid], False, r))
            elif k == "remove_dead_key":
                for path in (f_settings, f_local):
                    cur = load(path, {}) or {}
                    if "disabledMcpServers" in cur:
                        e = ensure(path)
                        e["mut"].append((["disabledMcpServers"], "__DELETE__", r))
        planned.append(plan)

    # ---- execute / dry-run ----
    report = {"mode": "apply" if apply else "dry-run", "ts": TS, "projects": [], "skipped": skipped}
    n_files = n_mut = 0
    for plan in planned:
        pr = {"slug": plan["slug"], "root": plan["root"], "files": []}
        for path, e in plan["files"].items():
            before = e["before"]
            after = copy.deepcopy(before)
            applied = []
            for keypath, val, r in e["mut"]:
                cur = after
                if val == "__DELETE__":
                    if keypath[0] in cur:
                        del cur[keypath[0]]
                        applied.append((".".join(keypath), "removed"))
                    continue
                for kp in keypath[:-1]:
                    cur = cur.setdefault(kp, {})
                cur[keypath[-1]] = val
                applied.append((".".join(keypath), val))
            n_mut += len(applied)
            n_files += 1
            fr = {"path": path, "existed": e["existed"], "mutations": applied,
                  "before": before, "after": after, "written": False, "verify": "dry-run"}
            if apply:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if e["existed"]:
                    shutil.copy2(path, path + ".bak." + TS)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(after, f, indent=2)
                os.replace(tmp, path)
                # on-disk verify
                live = load(path, {})
                ok = True
                for keypath, val, r in e["mut"]:
                    cur, miss = live, False
                    for kp in keypath:
                        if isinstance(cur, dict) and kp in cur:
                            cur = cur[kp]
                        else:
                            miss = True
                            break
                    if val == "__DELETE__":
                        if not miss:
                            ok = False
                    elif miss or cur != val:
                        ok = False
                # collateral: only intended flat keys changed
                bf, af = flatten(before), flatten(live)
                intended = set()
                for keypath, val, r in e["mut"]:
                    intended.add(".".join(repr(k) for k in keypath))
                changed = {k for k in set(bf) | set(af) if bf.get(k) != af.get(k)}
                collateral = sorted(c for c in changed
                                    if not any(c == ik or c.startswith(ik + ".") for ik in intended))
                if collateral:
                    ok = False
                    fr["collateral"] = collateral
                if e["existed"] and not os.path.isfile(path + ".bak." + TS):
                    ok = False
                fr["written"] = True
                fr["verify"] = "PASS" if ok else "FAIL"
            pr["files"].append(fr)
        report["projects"].append(pr)

    # ---- print summary ----
    by_kind = Counter()
    for plan in planned:
        for path, e in plan["files"].items():
            for _, _, r in e["mut"]:
                by_kind[r["kind"]] += 1
    print("=== cc-optimizer v2 apply (%s) ===" % report["mode"])
    print("projects touched: %d   files: %d   mutations: %d" % (len(planned), n_files, n_mut))
    for k, c in by_kind.most_common():
        print("   %-18s %d" % (k, c))
    if skipped:
        print("skipped %d project(s):" % len(skipped))
        for s, why in skipped[:10]:
            print("   %s — %s" % (s[-48:], why))
    if apply:
        fails = [(pr["slug"], fr["path"]) for pr in report["projects"]
                 for fr in pr["files"] if fr["verify"] == "FAIL"]
        passes = sum(1 for pr in report["projects"] for fr in pr["files"] if fr["verify"] == "PASS")
        print("\nverify: %d PASS · %d FAIL" % (passes, len(fails)))
        for s, p in fails[:20]:
            print("   FAIL", s[-40:], p)
    else:
        # show a few sample projects
        print("\nsample plan (first 3 projects):")
        for pr in report["projects"][:3]:
            print("  %s" % pr["slug"][-50:])
            for fr in pr["files"]:
                for kp, v in fr["mutations"]:
                    print("     %s = %s   (%s)" % (kp, v, os.path.basename(fr["path"])))

    if report_path:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print("\nreport -> %s" % report_path)


if __name__ == "__main__":
    main()
