#!/usr/bin/env python3
"""
cc-optimizer - independent post-apply verifier (the "check depois").

Reads the before/after audit written by `apply.py --report` and confirms, by
re-reading the live files on disk, that each apply was correct and surgical:
  - the current settings.json parses as valid JSON;
  - every intended mutation (key=value) is present on disk;
  - the only keys that changed from `before` are the intended ones (no
    collateral edits — deep-merge preserved everything else);
  - a timestamped .bak exists for any file that pre-existed and was written.

  python3 verify_apply.py <report.json>

Exit code 0 if all projects PASS, 1 otherwise.
"""
import sys, os, json, glob


def get_path(d, dotted):
    cur = d
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return (False, None)
        cur = cur[k]
    return (True, cur)


def flatten(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, prefix + k + "."))
    else:
        out[prefix.rstrip(".")] = d
    return out


def main():
    rep = json.load(open(sys.argv[1]))
    mode = rep.get("mode")
    print("=== verify_apply — auditing %s report (%d projects) ===\n" % (mode, len(rep["projects"])))
    fails = 0
    passes = 0
    for r in rep["projects"]:
        name = r["name"]
        path = r["settings_path"]
        problems = []

        # 1. determine the state to check.
        #    apply  -> the live file on disk (must parse);
        #    dry-run -> the planned end-state (nothing was written).
        if mode == "apply":
            if os.path.isfile(path):
                try:
                    live = json.load(open(path))
                except Exception as e:
                    live = {}
                    problems.append("current settings.json does NOT parse: %s" % e)
            else:
                live = {}
                problems.append("file missing after a written apply")
        else:
            live = r.get("after") or {}

        # 2. intended mutations present
        for m in r["mutations"]:
            ok, val = get_path(live, m["key"])
            if not ok or val != m["value"]:
                problems.append("mutation not present: %s=%s (found %r)" % (m["key"], m["value"], val))

        # 3. only intended keys changed (no collateral) — before vs end-state
        before_flat = flatten(r["before"])
        live_flat = flatten(live)
        intended = {m["key"] for m in r["mutations"]}
        collateral = {k for k in set(before_flat) | set(live_flat)
                      if before_flat.get(k) != live_flat.get(k)} - intended
        if collateral:
            problems.append("collateral key changes (unexpected): %s" % sorted(collateral))

        # 4. backup exists for pre-existing files that were actually written
        if mode == "apply" and r.get("written") and r.get("existed_before"):
            if not glob.glob(path + ".bak.*"):
                problems.append("no .bak backup found for a pre-existing file")

        if problems:
            fails += 1
            print("✘ %s" % name)
            for p in problems:
                print("    - %s" % p)
        else:
            passes += 1
            print("✓ %s  (%d mutations verified)" % (name, len(r["mutations"])))

    print("\n=== %d PASS · %d FAIL ===" % (passes, fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
