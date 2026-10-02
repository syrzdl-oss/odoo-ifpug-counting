#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""measure_mean_ftr.py - mean FTR of report EOs and wizard EIs on the development set.

Two declared functions have FTRs that declarations show only in part:
  * a report EO reads the report model plus every table traversed by the
    resolvable output paths of its QWeb template (with the whole t-call
    closure, resolved by QWeb scoping; see :templateFtrRef);
  * a wizard EI writes the tables targeted by the wizard's relational fields.
Both are measured statically (no AI) on the development-set modules; subgroups
are folded into their parents and only persistent, user-recognizable tables
count. The means are written as :meanFtr cells and rounded when applied.

Usage:
  python scripts/measure_mean_ftr.py --abox out/abox.ttl --develop-set data/develop_set.txt \\
         --out data/calib_ftr.ttl
"""
import argparse
import ast
import json
import os
import re
import sys
from collections import Counter, defaultdict

from rdflib import Graph

NS = "http://example.org/odoo-ifpug#"
REL_TYPES = {"many2one", "one2many", "many2many"}
ROOT_TOKENS = {"doc", "docs", "o", "object", "obj", "record", "line", "l", "self"}



def load_module_list(path, fallback=""):
    import os
    import sys
    if path and os.path.exists(path):
        out = []
        for line in open(path, encoding="utf-8-sig"):
            line = line.split("#")[0].strip()
            if line:
                out.append(line)
        if out:
            print(f"[develop set] {len(out)} modules from {path}")
            return set(out)
    fb = {m.strip() for m in (fallback or "").split(",") if m.strip()}
    if fb:
        print(f"[develop set] {len(fb)} modules from --calib-modules")
        return fb
    sys.exit(f"[fatal] develop-set file {path!r} not found and --calib-modules not given.\n"
             f"        To measure on all modules, pass --calib-modules all explicitly.")

def load_facts(abox_path, sig_path=None):
    g = Graph()
    g.parse(abox_path, format="turtle")
    if sig_path and os.path.exists(sig_path):
        g.parse(sig_path, format="turtle")
    q = lambda s: list(g.query("PREFIX : <%s> " % NS + s))

    name = {str(r[0]): str(r[1]) for r in q("SELECT ?m ?n WHERE { ?m :modelName ?n }")}
    host = {str(r[0]): str(r[1]).split("#module_")[-1]
            for r in q("SELECT ?m ?h WHERE { ?m :hostModule ?h }")}

    apps = {str(r[0]).split("#module_")[-1]
            for r in q("SELECT ?m WHERE { ?m :isApplication true }")}
    inscope = {str(r[0]) for r in q("""SELECT DISTINCT ?m WHERE { ?m a :PersistentModel .
                 FILTER(EXISTS { ?f :belongsTo ?m ; :isUserVisible true } ||
                        EXISTS { ?a a :WindowAction ; :actsOn ?m ; :reachKind ?k . FILTER(?k != "none") }) }""")}
    fold = {str(r[0]): str(r[1]) for r in q("SELECT ?c ?p WHERE { ?c :foldedInto ?p }")}
    print(f"[scope] tables that can be FTRs (persistent, recognizable): {len(inscope)}; subgroups: {len(fold)}")
    transient = {str(r[0]) for r in q("SELECT ?m WHERE { ?m a :TransientModel }")}

    rel = {}
    for r in q("""SELECT ?m ?fn ?t WHERE {
                    ?f :belongsTo ?m ; :fieldName ?fn ; :relatesTo ?t }"""):
        rel[(str(r[0]), str(r[1]))] = str(r[2])

    owner_of = {str(r[0]): str(r[1])
                for r in q("SELECT ?f ?m WHERE { ?f :belongsTo ?m }")}
    tpl_refs, tpl_calls, tpl_place = defaultdict(set), defaultdict(set), {}
    for r in q("SELECT ?t ?m WHERE { ?t :templateFtrRef ?m }"):
        tpl_refs[str(r[0])].add(str(r[1]))
    for r in q("SELECT ?t ?c WHERE { ?t :tCalls ?c }"):
        tpl_calls[str(r[0])].add(str(r[1]))
    for r in q("SELECT ?t ?nf ?ne WHERE { ?t :nTField ?nf ; :nTEsc ?ne }"):
        tpl_place[str(r[0])] = int(r[1]) + int(r[2])
    reports = defaultdict(lambda: {"model": None, "fields": set(), "n_place": 0, "tpl": None})
    for r in q("""SELECT ?a ?m ?nf ?ne WHERE {
                    ?a a :ReportAction ; :actsOn ?m ; :usesTemplate ?t .
                    ?t :nTField ?nf ; :nTEsc ?ne }"""):
        e = reports[str(r[0])]
        e["model"] = str(r[1])
        e["n_place"] = int(r[2]) + int(r[3])
    for r in q("SELECT ?a ?t WHERE { ?a a :ReportAction ; :usesTemplate ?t }"):
        reports[str(r[0])]["tpl"] = str(r[1])
    for r in q("""SELECT ?a ?m ?f WHERE {
                    ?a a :ReportAction ; :actsOn ?m ; :usesTemplate ?t .
                    ?t :templateDetField ?f }"""):
        e = reports[str(r[0])]
        e["model"] = str(r[1])
        e["fields"].add(str(r[2]))

    wizards = {str(r[0]) for r in q("""SELECT DISTINCT ?m WHERE {
                    ?m a :TransientModel .
                    ?a a :WindowAction ; :actsOn ?m ; :reachKind ?k ;
                       :viewModeToken "form" . FILTER(?k != "none") }""")}
    name2uri = {v: k for k, v in name.items()}
    return dict(name=name, name2uri=name2uri, host=host, inscope=inscope, fold=fold,
                tpl_refs=tpl_refs, tpl_calls=tpl_calls, tpl_place=tpl_place,
                owner_of=owner_of,
                transient=transient, rel=rel, reports=reports, wizards=wizards)


# ─────────────────────────── EO-report ───────────────────────────


def attr_chains(expr):
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        return set()

    local = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)):
            for gen in node.generators:
                if isinstance(gen.target, ast.Name):
                    local[gen.target.id] = gen.iter

    def chain(node, depth=0):
        if depth > 6:
            return None
        if isinstance(node, ast.Name):
            if node.id in local:
                inner = chain(local[node.id], depth + 1)
                return inner
            return node.id
        if isinstance(node, ast.Attribute):
            base = chain(node.value, depth + 1)
            return f"{base}.{node.attr}" if base else None
        if isinstance(node, ast.Subscript):
            return chain(node.value, depth + 1)
        if isinstance(node, ast.Call):
            return chain(node.func, depth + 1)      # o.mapped(...) -> o
        return None

    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            ch = chain(node)
            if ch and "." in ch:
                out.add(ch)
    return out


def resolve_path(model, path, rel):
    hit = set()
    segs = [s.strip("] ") for s in re.split(r"[.\[]", path) if s and not s.endswith("]")]
    if not segs:
        return hit, False
    cur = model
    if segs[0].lower() in ROOT_TOKENS:
        segs = segs[1:]
        rooted = True
    elif (model, segs[0]) in rel:
        rooted = True
    else:
        return hit, False
    for s in segs:
        tgt = rel.get((cur, s))
        if tgt is None:
            break
        hit.add(tgt)
        cur = tgt
    return hit, rooted


def measure_reports(F, keep_modules):
    rows = []
    for aid, e in F["reports"].items():
        m = e["model"]
        if not m or F["fold"].get(m, m) not in F["inscope"]:
            continue
        if keep_modules and F["host"].get(m) not in keep_modules:
            continue
        touched = {m} | F["tpl_refs"].get(e["tpl"], set())
        seen, place = {e["tpl"]}, F["tpl_place"].get(e["tpl"], 0)
        touched |= {F["owner_of"].get(fu) for fu in e["fields"]}
        touched = {F["fold"].get(x, x) for x in touched if x}
        touched = {x for x in touched if x in F["inscope"]}
        rows.append((aid, m, max(len(touched), 1), len(seen), place))
    return rows


# ─────────────────────────── Wizard ───────────────────────────




def measure_wizards(F, keep_modules):
    by_name = {v: k for k, v in F["name"].items()}
    rows = []
    for w in F["wizards"]:
        if keep_modules and F["host"].get(w) not in keep_modules:
            continue
        wname = F["name"].get(w)
        if not wname:
            continue
        touched = {w}
        for (m, _fn), tgt in F["rel"].items():
            if m == w:
                touched.add(tgt)
        dyn = False
        keep = {F["fold"].get(t, t) for t in touched}
        keep = {t for t in keep if t in F["inscope"] and t not in F["transient"]}
        rows.append((w, wname, max(len(keep), 1), dyn))
    return rows


def emit(rep_rows, wiz_rows, out_path):
    def stat(vals):
        n = len(vals)
        mean = sum(vals) / n if n else 2.0
        return n, mean, Counter(vals)

    n_r, mean_r, dist_r = stat([r[2] for r in rep_rows])
    n_w, mean_w, dist_w = stat([r[2] for r in wiz_rows])
    fmt = lambda d: ",".join(f"{k}:{v}" for k, v in sorted(d.items()))

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("@prefix : <%s> .\n" % NS)
        f.write("@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n\n")
        f.write("# Mean FTR measured statically on the development set; applied to all modules.\n\n")
        for kind, cat, n, mean, dist in (
                ("eo_report", "EO", n_r, mean_r, dist_r),
                ("wizard", "EI", n_w, mean_w, dist_w)):
            f.write(f":calib_ftr_{kind} a :CalibrationStat ;\n")
            f.write(f'    :entryKind "{kind}" ; :txCategory "{cat}" ;\n')
            f.write(f'    :meanFtr "{mean:.4f}"^^xsd:decimal .\n\n')
    return (n_r, mean_r, dist_r), (n_w, mean_w, dist_w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--abox", required=True)
    ap.add_argument("--signatures")
    ap.add_argument("--calib-modules", default="",
                    help="development-set modules, comma separated (overridden by --develop-set if the file exists)")

    ap.add_argument("--develop-set", default="develop_set.txt",

                    help="file listing the development-set modules")
    ap.add_argument("--out", default="calib_ftr.ttl")
    a = ap.parse_args()

    keep = load_module_list(a.develop_set, a.calib_modules)
    keep = set() if keep == {"all"} else keep
    F = load_facts(a.abox, a.signatures)
    print(f"[facts] report actions {len(F['reports'])} · wizards {len(F['wizards'])} · "
          f"relational fields {len(F['rel'])}")

    rep = measure_reports(F, keep)
    ntpl = sum(r[3] for r in rep); tot = sum(r[4] for r in rep)
    print(f"[report] measured {len(rep)} reports ({ntpl} root templates, {tot} output items)")

    wiz = measure_wizards(F, keep)
    print(f"[wizard] measured {len(wiz)} wizards (evidence: the wizard's own relational fields)")

    (nr, mr, dr), (nw, mw, dw) = emit(rep, wiz, a.out)
    print(f"\n[out] {a.out}")
    print(f"  eo_report : n={nr} meanFtr={mr:.3f} dist={dict(dr)}")
    print(f"  wizard    : n={nw} meanFtr={mw:.3f} dist={dict(dw)}")


if __name__ == "__main__":
    main()
