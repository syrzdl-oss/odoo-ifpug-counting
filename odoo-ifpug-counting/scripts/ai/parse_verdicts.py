#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parse_verdicts.py — validate verdicts.jsonl, write verdicts.ttl.

Usage:
  python scripts/ai/parse_verdicts.py --jsonl out/verdicts.jsonl --cand out/candidates.json \\
         --out data/verdicts.ttl [--run-id batch1]
"""
import argparse, json, sys

NS = "http://example.org/odoo-ifpug#"
CATS = {"EI", "EO", "EQ"}
NOS = {"forward", "technical", "unreachable", "duplicate"}
CONF = {"high", "med", "low"}


def esc(t):
    return str(t).replace("\\", "\\\\").replace('"', '\\"')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--cand", required=True)
    ap.add_argument("--out", default="verdicts.ttl")
    ap.add_argument("--run-id", default="run1")
    args = ap.parse_args()

    cand = {c["id"]: c for c in json.load(open(args.cand, encoding="utf-8"))}
    ttl = ["@prefix : <%s> ." % NS, ""]
    seen, n_yes, errors, superseded = set(), 0, 0, []
    records = {}

    for ln, raw in enumerate(open(args.jsonl, encoding="utf-8"), 1):
        raw = raw.strip()
        if not raw or raw.startswith("```"):
            continue
        if not raw.startswith("{"):          # chat replies sometimes wrap lines
            b = raw.find("{")
            if b < 0:
                continue                      # prose line from a chat paste
            raw = raw[b:]
        if raw.endswith(","):
            raw = raw[:-1]
        try:
            v = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[line {ln}] bad JSON: {e}"); errors += 1; continue
        cid = v.get("id")
        if cid not in cand:
            print(f"[line {ln}] unknown id {cid}"); errors += 1; continue
        if cid in seen:
            superseded.append(cid)          # rerun fixed an earlier failure
        seen.add(cid)
        vd = v.get("verdict")
        if vd not in ("yes", "no"):
            print(f"[line {ln}] {cid}: verdict must be yes|no"); errors += 1; continue
        if vd == "yes":
            if v.get("category") not in CATS:
                print(f"[line {ln}] {cid}: yes needs category EI|EO|EQ")
                errors += 1; continue
        else:
            if v.get("no_reason") not in NOS:
                print(f"[line {ln}] {cid}: no needs no_reason"); errors += 1; continue
        if v.get("confidence") not in CONF:
            print(f"[line {ln}] {cid}: bad confidence"); errors += 1; continue
        for key in ("det_fields", "ftr_tables"):
            if not isinstance(v.get(key), list):
                print(f"[line {ln}] {cid}: {key} must be a list"); errors += 1
                v[key] = []

        c = cand[cid]
        subj = f'<{c["uri"]}>' if c.get("uri") else f":{cid}"
        t = [f'{subj} :aiVerdict2 "{vd}"']
        t.append(f':aiConfidence2 "{v["confidence"]}"')
        t.append(f':aiRunId2 "{esc(v.get("run_id") or args.run_id)}"')
        if v.get("model"):
            t.append(f':aiModel2 "{esc(v["model"])}"')
        t.append(f':aiReasons2 "{esc(v.get("reasons", ""))[:200]}"')
        if vd == "yes":
            n_yes += 1
            t.append(f':aiCategory2 "{v["category"]}"')
            for f_ in v["det_fields"]:
                t.append(f':aiDetField2 "{esc(f_)}"')
            for tb in v["ftr_tables"]:
                t.append(f':aiFtrTable2 "{esc(tb)}"')
        else:
            t.append(f':aiNoReason2 "{v["no_reason"]}"')
        records[cid] = " ;\n    ".join(t) + " ."

    ttl.extend(records.values())
    open(args.out, "w", encoding="utf-8").write("\n".join(ttl) + "\n")
    if superseded:
        print(f"[note] {len(superseded)} id(s) judged more than once; kept the last")
    missing = set(cand) - seen
    print(f"[verdicts] {len(seen)} parsed ({n_yes} yes), {errors} errors, "
          f"{len(missing)} candidates still unjudged -> {args.out}")


if __name__ == "__main__":
    main()
