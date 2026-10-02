#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_packets.py — one self-contained judgement packet per candidate.

Each packet = header + structural context + code slice + full RUBRIC.
Feed one packet per fresh LLM session; collect one JSON line each into
verdicts.jsonl.

Usage:
  python scripts/ai/make_packets.py --cand out/candidates.json --odoo ODOO_ROOT \\
         --abox out/abox.ttl out/signatures.ttl --rubric scripts/ai/RUBRIC.md \\
         --outdir out/packets --only-modules hr,fleet,mass_mailing,survey,point_of_sale,crm,stock,mrp
"""
import argparse, ast, json, os, sys
from rdflib import Graph

NS = "http://example.org/odoo-ifpug#"
MAX_LINES = 120


def code_slice(locator):
    """locator 'file:line' -> source of the def starting at that line."""
    if locator in ("", "UNRESOLVED") or locator.startswith("XMLID:"):
        return None
    path, _, line = locator.rpartition(":")
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return None
    line = int(line)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.lineno == line:
            lines = src.splitlines()[node.lineno - 1: node.end_lineno]
            if len(lines) > MAX_LINES:
                lines = lines[:MAX_LINES] + ["    # … truncated …"]
            return "\n".join(lines)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cand", required=True)
    ap.add_argument("--odoo", required=True)
    ap.add_argument("--abox", required=True, nargs="+")
    ap.add_argument("--signatures", default=None,
                    help="signatures.ttl (for server code text)")
    ap.add_argument("--rubric", required=True)
    ap.add_argument("--outdir", default="packets")
    ap.add_argument("--only-modules", help="comma list: generate packets ONLY "
                    "for these modules (e.g. the develop set)")
    ap.add_argument("--skip-modules", help="comma list: exclude these modules")
    ap.add_argument("--kinds", default="button,route,server", help="comma list of entry kinds (default: button,route,server)")
    args = ap.parse_args()

    rubric = open(args.rubric, encoding="utf-8").read()
    cands = json.load(open(args.cand, encoding="utf-8"))
    n_all = len(cands)
    only = set(args.only_modules.split(",")) if args.only_modules else None
    skip = set(args.skip_modules.split(",")) if args.skip_modules else None
    kinds = set(args.kinds.split(","))
    cands = [c for c in cands
             if (only is None or c.get("module") in only)
             and (skip is None or c.get("module") not in skip)
             and (kinds is None or c["kind"] in kinds)]
    if len(cands) != n_all:
        from collections import Counter
        print(f"[filter] {n_all} candidates -> {len(cands)} selected "
              f"{dict(Counter(c['kind'] for c in cands))}")
    os.makedirs(args.outdir, exist_ok=True)

    g = Graph()
    for f in args.abox + ([args.signatures] if args.signatures else []):
        g.parse(f, format="turtle")
    q = lambda s: list(g.query("PREFIX : <%s> " % NS + s))

    srv_text = {str(r[0]): str(r[1]) for r in
                q("SELECT ?s ?c WHERE { ?s :srvCodeText ?c . }")}

    manifest = []
    for c in cands:
        cid, kind = c["id"], c["kind"]
        parts = [f"# PACKET {cid}", "",
                 f"kind: {kind}   module: {c.get('module')}   "
                 f"model: {c.get('model', '-')}",
                 f"locator: {c.get('locator')}"]
        if kind == "button":
            parts.append(f"button method: {c.get('method')}")
            if c.get("locator") == "UNRESOLVED":
                parts.append("NOTE: body not located — judge from name + "
                             "model semantics; confidence at most 'med'.")
        if kind == "route":
            parts.append("route paths: " + ", ".join(c.get("paths") or ["?"]))

        code = None
        if kind == "server":
            code = srv_text.get(c.get("uri", ""))
            if code is None and c["locator"].startswith("XMLID:"):
                parts.append("NOTE: code field not pre-extracted; "
                             "locate by xmlid " + c["xmlid"])
        else:
            code = code_slice(c.get("locator", ""))
        parts.append("")
        parts.append("## code")
        parts.append("```python\n" + (code or "(not available)") + "\n```")
        parts.append("")
        parts.append(rubric)
        path = os.path.join(args.outdir, f"{cid}.md")
        open(path, "w", encoding="utf-8").write("\n".join(parts))
        manifest.append(f"{cid}\t{kind}\t{path}")

    open(os.path.join(args.outdir, "MANIFEST.tsv"), "w").write(
        "\n".join(manifest) + "\n")
    print(f"[packets] {len(cands)} written to {args.outdir}/")


if __name__ == "__main__":
    main()
