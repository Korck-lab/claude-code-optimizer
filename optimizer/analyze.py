#!/usr/bin/env python3
"""
cc-optimizer - deterministic session-log aggregator.

Reads Claude Code session transcripts (.jsonl) and produces per-project
quota/cost aggregates WITHOUT spending any model tokens. The structured
`usage` and `model` fields are already recorded in every assistant line,
so all spend analysis here is pure arithmetic.

It intentionally hardcodes NO pricing and NO config-knob knowledge: those
come from optimizer/knowledge-base.json (built from fresh official docs).
This script only measures; cost weighting & recommendations happen later.

Reusable / parameterized:  python3 analyze.py <sessions_dir> <out.json>
Each top-level subdirectory of <sessions_dir> is treated as one "project"
(the dir name is the project slug, e.g. -Users-me-Documents-sources-foo).
"""
import sys, os, json, glob
from collections import defaultdict


def new_model_bucket():
    return {
        "msgs": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "ephemeral_1h_input_tokens": 0,
        "ephemeral_5m_input_tokens": 0,
        # output-size histogram: cheap signal for "expensive model on trivial work"
        "tiny_msgs": 0,    # output_tokens < 300  (likely Haiku-able)
        "tiny_billable": 0,
        "small_msgs": 0,   # 300 <= output_tokens < 1000
        "small_billable": 0,
    }


def new_project():
    return {
        "sessions": 0,
        "lines": 0,
        "assistant_msgs": 0,
        "user_msgs": 0,
        "by_model": defaultdict(new_model_bucket),
        "main_tokens": 0,        # isSidechain false/absent
        "sub_tokens": 0,         # isSidechain true (subagent work)
        "sub_by_model": defaultdict(int),  # subagent billable tokens by model
        "mcp_calls": defaultdict(int),     # attributionMcpServer -> count
        "mcp_tool_calls": defaultdict(int),# attributionMcpTool -> count
        "web_search_requests": 0,
        "web_fetch_requests": 0,
        "service_tier": defaultdict(int),
        "versions": defaultdict(int),
        "first_ts": None,
        "last_ts": None,
        "max_session_input_tokens": 0,     # largest single per-turn context seen
        "base_contexts": [],               # per-session first-assistant base context
    }


def billable(u):
    """Total tokens that touch a context window for an assistant turn."""
    return (
        int(u.get("input_tokens", 0) or 0)
        + int(u.get("output_tokens", 0) or 0)
        + int(u.get("cache_creation_input_tokens", 0) or 0)
        + int(u.get("cache_read_input_tokens", 0) or 0)
    )


def pct(xs, q):
    if not xs:
        return 0
    s = sorted(xs)
    i = min(len(s) - 1, int(q * (len(s) - 1) + 0.5))
    return s[i]


def main():
    if len(sys.argv) < 3:
        print("usage: analyze.py <sessions_dir> <out.json>", file=sys.stderr)
        sys.exit(2)
    root = os.path.abspath(sys.argv[1])
    out_path = os.path.abspath(sys.argv[2])

    projects = defaultdict(new_project)
    bad_lines = 0
    total_files = 0

    for proj_dir in sorted(os.listdir(root)):
        full = os.path.join(root, proj_dir)
        if not os.path.isdir(full):
            continue
        p = projects[proj_dir]
        for fp in glob.glob(os.path.join(full, "**", "*.jsonl"), recursive=True):
            p["sessions"] += 1
            total_files += 1
            file_first_base = None     # base context of this session
            try:
                f = open(fp, "r", encoding="utf-8", errors="replace")
            except OSError:
                continue
            with f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    p["lines"] += 1
                    try:
                        o = json.loads(line)
                    except Exception:
                        bad_lines += 1
                        continue
                    t = o.get("type")
                    ts = o.get("timestamp")
                    if ts:
                        if p["first_ts"] is None or ts < p["first_ts"]:
                            p["first_ts"] = ts
                        if p["last_ts"] is None or ts > p["last_ts"]:
                            p["last_ts"] = ts
                    v = o.get("version")
                    if v:
                        p["versions"][v] += 1
                    srv = o.get("attributionMcpServer")
                    if srv:
                        p["mcp_calls"][srv] += 1
                    tool = o.get("attributionMcpTool")
                    if tool:
                        p["mcp_tool_calls"][tool] += 1
                    if t == "user":
                        p["user_msgs"] += 1
                        continue
                    if t != "assistant":
                        continue
                    p["assistant_msgs"] += 1
                    msg = o.get("message") or {}
                    model = msg.get("model") or "unknown"
                    u = msg.get("usage") or {}
                    b = p["by_model"][model]
                    b["msgs"] += 1
                    it = int(u.get("input_tokens", 0) or 0)
                    ot = int(u.get("output_tokens", 0) or 0)
                    cc = int(u.get("cache_creation_input_tokens", 0) or 0)
                    cr = int(u.get("cache_read_input_tokens", 0) or 0)
                    b["input_tokens"] += it
                    b["output_tokens"] += ot
                    b["cache_creation_input_tokens"] += cc
                    b["cache_read_input_tokens"] += cr
                    cache = u.get("cache_creation") or {}
                    b["ephemeral_1h_input_tokens"] += int(cache.get("ephemeral_1h_input_tokens", 0) or 0)
                    b["ephemeral_5m_input_tokens"] += int(cache.get("ephemeral_5m_input_tokens", 0) or 0)
                    bill = it + ot + cc + cr
                    if ot < 300:
                        b["tiny_msgs"] += 1
                        b["tiny_billable"] += bill
                    elif ot < 1000:
                        b["small_msgs"] += 1
                        b["small_billable"] += bill
                    stu = u.get("server_tool_use") or {}
                    p["web_search_requests"] += int(stu.get("web_search_requests", 0) or 0)
                    p["web_fetch_requests"] += int(stu.get("web_fetch_requests", 0) or 0)
                    tier = u.get("service_tier")
                    if tier:
                        p["service_tier"][tier] += 1
                    if o.get("isSidechain"):
                        p["sub_tokens"] += bill
                        p["sub_by_model"][model] += bill
                    else:
                        p["main_tokens"] += bill
                    # per-turn context proxy = input + cache_read (what got re-fed)
                    ctx = it + cr
                    if ctx > p["max_session_input_tokens"]:
                        p["max_session_input_tokens"] = ctx
                    # base context = first assistant turn of the session = fixed
                    # overhead (system prompt + tool schemas + CLAUDE.md + MCP)
                    if file_first_base is None:
                        file_first_base = it + cc + cr
            if file_first_base is not None:
                p["base_contexts"].append(file_first_base)

    # finalize -> plain dicts + derived metrics (skip 0-session noise dirs)
    out_projects = {}
    g = {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        "ephemeral_1h_input_tokens": 0, "by_model_tokens": defaultdict(int),
        "assistant_msgs": 0, "sessions": 0, "projects": 0,
    }
    for name, p in projects.items():
        if p["sessions"] == 0:
            continue
        by_model = {}
        proj_in = proj_out = proj_cc = proj_cr = proj_1h = 0
        for m, b in p["by_model"].items():
            by_model[m] = dict(b)
            proj_in += b["input_tokens"]; proj_out += b["output_tokens"]
            proj_cc += b["cache_creation_input_tokens"]; proj_cr += b["cache_read_input_tokens"]
            proj_1h += b["ephemeral_1h_input_tokens"]
            g["by_model_tokens"][m] += b["input_tokens"] + b["output_tokens"] + b["cache_creation_input_tokens"] + b["cache_read_input_tokens"]
        denom = proj_in + proj_cc + proj_cr
        cache_hit_ratio = round(proj_cr / denom, 4) if denom else 0.0
        total_tokens = proj_in + proj_out + proj_cc + proj_cr
        bc = p["base_contexts"]
        out_projects[name] = {
            "sessions": p["sessions"],
            "lines": p["lines"],
            "assistant_msgs": p["assistant_msgs"],
            "user_msgs": p["user_msgs"],
            "total_tokens": total_tokens,
            "input_tokens": proj_in,
            "output_tokens": proj_out,
            "cache_creation_input_tokens": proj_cc,
            "cache_read_input_tokens": proj_cr,
            "cache_hit_ratio": cache_hit_ratio,
            "ephemeral_1h_input_tokens": proj_1h,
            "by_model": by_model,
            "main_tokens": p["main_tokens"],
            "sub_tokens": p["sub_tokens"],
            "sub_by_model": dict(p["sub_by_model"]),
            "distinct_mcp_servers": len(p["mcp_calls"]),
            "mcp_calls": dict(sorted(p["mcp_calls"].items(), key=lambda kv: -kv[1])),
            "mcp_tool_calls": dict(sorted(p["mcp_tool_calls"].items(), key=lambda kv: -kv[1])[:25]),
            "web_search_requests": p["web_search_requests"],
            "web_fetch_requests": p["web_fetch_requests"],
            "service_tier": dict(p["service_tier"]),
            "versions": dict(p["versions"]),
            "first_ts": p["first_ts"],
            "last_ts": p["last_ts"],
            "max_session_input_tokens": p["max_session_input_tokens"],
            "base_context_min": min(bc) if bc else 0,
            "base_context_median": pct(bc, 0.5),
            "base_context_p90": pct(bc, 0.9),
            "base_context_max": max(bc) if bc else 0,
        }
        g["input_tokens"] += proj_in; g["output_tokens"] += proj_out
        g["cache_creation_input_tokens"] += proj_cc; g["cache_read_input_tokens"] += proj_cr
        g["ephemeral_1h_input_tokens"] += proj_1h
        g["assistant_msgs"] += p["assistant_msgs"]; g["sessions"] += p["sessions"]
        g["projects"] += 1

    gdenom = g["input_tokens"] + g["cache_creation_input_tokens"] + g["cache_read_input_tokens"]
    g["cache_hit_ratio"] = round(g["cache_read_input_tokens"] / gdenom, 4) if gdenom else 0.0
    g["by_model_tokens"] = dict(g["by_model_tokens"])
    g["total_tokens"] = g["input_tokens"] + g["output_tokens"] + g["cache_creation_input_tokens"] + g["cache_read_input_tokens"]

    result = {
        "schema_version": 2,
        "sessions_dir": root,
        "files_scanned": total_files,
        "bad_lines": bad_lines,
        "global": g,
        "projects": out_projects,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)

    # concise stdout summary
    ranked = sorted(out_projects.items(), key=lambda kv: -kv[1]["total_tokens"])
    print("files_scanned=%d bad_lines=%d projects=%d" % (total_files, bad_lines, len(out_projects)))
    print("GLOBAL total_tokens=%d (in=%d out=%d cache_create=%d cache_read=%d) cache_hit=%.2f%%" % (
        g["total_tokens"], g["input_tokens"], g["output_tokens"],
        g["cache_creation_input_tokens"], g["cache_read_input_tokens"], g["cache_hit_ratio"] * 100))
    print("by_model_tokens:")
    for m, tk in sorted(g["by_model_tokens"].items(), key=lambda kv: -kv[1]):
        print("  %-32s %15d" % (m, tk))
    print("TOP 15 projects by total_tokens:")
    for name, d in ranked[:15]:
        print("  %-50s tok=%-13d hit=%2.0f%% sub=%-11d 1h=%-10d base_med=%d" % (
            name[-50:], d["total_tokens"], d["cache_hit_ratio"] * 100,
            d["sub_tokens"], d["ephemeral_1h_input_tokens"], d["base_context_median"]))
    print("wrote", out_path)


if __name__ == "__main__":
    main()
