#!/usr/bin/env python3
"""aggregate_params.py - aggregate AI verdicts into the code-side parameters.

For every entry kind (button, route, server action) and transaction category
(EI, EO, EQ) the script computes

    p = admitted candidates of that category / judged candidates of that kind
    m = mean UFP of the admitted candidates

The UFP of an admitted candidate is rated with the IFPUG complexity matrix of
its category, using the DET fields and FTR tables listed in its verdict
(DET = number of listed fields, falling back to 8 when none are listed;
FTR = number of listed tables, at least 1).

Every candidate of the development set is judged, so the judged candidates of
a kind are the denominator of p. The entry kind is read from the candidate
identifier prefix (btn_, rt_, sact_); other kinds are ignored.

Usage:
    python scripts/ai/aggregate_params.py --verdicts data/verdicts.ttl --out data/calib_params.ttl
"""
import argparse
from collections import Counter, defaultdict

from rdflib import Graph

NS = "http://example.org/odoo-ifpug#"
DET_FALLBACK = 8
KIND_BY_PREFIX = {"btn_": "button", "rt_": "route", "sact_": "server"}


def kind_of(subject):
    local = subject.split("#")[-1]
    for prefix, kind in KIND_BY_PREFIX.items():
        if local.startswith(prefix):
            return kind
    return None


def ifpug_weight(cat, det, ftr):
    """Weight of one transaction function by the IFPUG complexity matrix."""
    if cat == "EI":
        d = 0 if det <= 4 else 1 if det <= 15 else 2
        f = 0 if ftr <= 1 else 1 if ftr == 2 else 2
        return [[3, 3, 4], [3, 4, 6], [4, 6, 6]][f][d]
    d = 0 if det <= 5 else 1 if det <= 19 else 2
    f = 0 if ftr <= 1 else 1 if ftr <= 3 else 2
    table = [[4, 4, 5], [4, 5, 7], [5, 7, 7]] if cat == "EO" else [[3, 3, 4], [3, 4, 6], [4, 6, 6]]
    return table[f][d]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verdicts", required=True, help="verdict graph written by parse_verdicts.py")
    ap.add_argument("--out", default="calib_params.ttl")
    args = ap.parse_args()

    g = Graph()
    g.parse(args.verdicts, format="turtle")
    q = lambda s: list(g.query(f"PREFIX : <{NS}> " + s))

    judged = Counter()
    for (s,) in q("SELECT DISTINCT ?s WHERE { ?s :aiVerdict2 ?v }"):
        k = kind_of(str(s))
        if k:
            judged[k] += 1
    det_n = {str(s): int(n) for s, n in q("SELECT ?s (COUNT(?f) AS ?n) WHERE { ?s :aiDetField2 ?f } GROUP BY ?s")}
    ftr_n = {str(s): int(n) for s, n in q("SELECT ?s (COUNT(?t) AS ?n) WHERE { ?s :aiFtrTable2 ?t } GROUP BY ?s")}

    weights = defaultdict(list)
    for s, cat in q('SELECT ?s ?c WHERE { ?s :aiVerdict2 "yes" ; :aiCategory2 ?c }'):
        s, cat = str(s), str(cat)
        k = kind_of(s)
        if k:
            weights[(k, cat)].append(ifpug_weight(cat, det_n.get(s) or DET_FALLBACK, max(1, ftr_n.get(s, 0))))

    lines = [f"@prefix : <{NS}> .", "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .", ""]
    print(f"{'entry':<8} {'cat':<4} {'judged':>6} {'admitted':>8} {'p':>7} {'m':>6}")
    for (k, cat), ws in sorted(weights.items()):
        p, m = len(ws) / judged[k], sum(ws) / len(ws)
        print(f"{k:<8} {cat:<4} {judged[k]:>6} {len(ws):>8} {p:>7.4f} {m:>6.3f}")
        lines.append(f':calib_{k}_{cat} a :CalibrationStat ;\n'
                     f'    :entryKind "{k}" ; :txCategory "{cat}" ;\n'
                     f'    :pValue "{p:.4f}"^^xsd:decimal ;\n'
                     f'    :mValue "{m:.3f}"^^xsd:decimal .')
    open(args.out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
