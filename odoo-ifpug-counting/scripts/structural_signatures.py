#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""structural_signatures.py - code-side candidates and their structural facts.

Reads signatures only (never interprets a method body):
  * buttons: for every object button on a persistent model in the ABox, locate
    the def of its method along the Python class hierarchy; the button is
    attributed to the module that first defines the method;
  * server actions (state "code"): capture the code text from the XML data
    file, as material for the AI verdict;
  * HTTP routes: locate @http.route-decorated functions; a route is attributed
    to the module of its controller.

Outputs:
  signatures.ttl   facts used by the counting rules (button declaredIn,
                   HttpRoute + inModule) and the server-action code text
  candidates.json  the worklist for AI adjudication:
                   [{id, kind, uri, module, model, locator, ...}, ...]

Usage:
  python scripts/structural_signatures.py --odoo ODOO_ROOT --abox out/abox.ttl \\
         --out-ttl out/signatures.ttl --out-cand out/candidates.json
"""
import argparse, ast, json, os, re, sys
from collections import defaultdict

try:
    from rdflib import Graph
except ImportError:
    sys.exit("pip install rdflib")

NS = "http://example.org/odoo-ifpug#"


def _slug(s):
    """Normalise a name into an IRI local name."""
    return re.sub(r"[^0-9A-Za-z_]", "_", str(s or "")).strip("_") or "x"


# ---------------------------------------------------------------- helpers
def iter_py(root):
    for dirpath, _dirs, files in os.walk(root):
        if "/tests" in dirpath.replace(os.sep, "/"):
            continue
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def iter_xml(root):
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".xml"):
                yield os.path.join(dirpath, f)


def module_of(path, odoo_root):
    rel = os.path.relpath(path, odoo_root).replace(os.sep, "/")
    parts = rel.split("/")
    if parts[0] == "addons" and len(parts) > 1:
        return parts[1]
    if parts[:2] == ["odoo", "addons"] and len(parts) > 2:
        return parts[2]
    return None


def esc(t):
    return t.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------- pass 1: python AST
def scan_python(odoo_root, wanted_modules):
    """One AST pass over the closure. Returns:
       class_index: model_name -> [ {file,line,module,methods:{name:line},
                                      inherits:[model_names]} ]
       route_defs:  list of {module,file,line,func,routes:[path,…]}"""
    class_index = defaultdict(list)
    route_defs = []
    for path in iter_py(odoo_root):
        mod = module_of(path, odoo_root)
        if mod is None or (wanted_modules and mod not in wanted_modules):
            continue
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names, inherits = [], []
                for b in node.body:
                    if isinstance(b, ast.Assign):
                        for t in b.targets:
                            tid = getattr(t, "id", "")
                            if tid == "_name" and isinstance(b.value, ast.Constant):
                                names.append(b.value.value)
                            if tid == "_inherit":
                                v = b.value
                                if isinstance(v, ast.Constant):
                                    inherits.append(v.value)
                                elif isinstance(v, (ast.List, ast.Tuple)):
                                    inherits += [e.value for e in v.elts
                                                 if isinstance(e, ast.Constant)]
                if not names and not inherits:
                    continue
                methods = {}
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods[item.name] = item.lineno
                model = names[0] if names else inherits[0]
                rec = dict(file=path, line=node.lineno, module=mod,
                           methods=methods, inherits=inherits, declares=bool(names))
                class_index[model].append(rec)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    d = dec
                    if isinstance(d, ast.Call):
                        d = d.func
                    dotted = []
                    while isinstance(d, ast.Attribute):
                        dotted.append(d.attr)
                        d = d.value
                    if isinstance(d, ast.Name):
                        dotted.append(d.id)
                    if "route" in dotted:
                        paths = []
                        if isinstance(dec, ast.Call) and dec.args:
                            a0 = dec.args[0]
                            if isinstance(a0, ast.Constant):
                                paths = [a0.value]
                            elif isinstance(a0, (ast.List, ast.Tuple)):
                                paths = [e.value for e in a0.elts
                                         if isinstance(e, ast.Constant)]
                        route_defs.append(dict(module=mod, file=path,
                                               line=node.lineno,
                                               func=node.name, paths=paths))
    return class_index, route_defs


def module_depth(odoo_root):
    """Depth of every module in the manifest dependency graph (base = 0).
    Used to pick, among modules defining the same method, the lowest one,
    i.e. the module that first defines it."""
    deps = {}
    for dirpath, _dirs, files in os.walk(odoo_root):
        if "__manifest__.py" in files:
            m = os.path.basename(dirpath)
            try:
                deps[m] = ast.literal_eval(open(os.path.join(dirpath, "__manifest__.py"),
                                                encoding="utf-8").read()).get("depends", [])
            except Exception:
                deps[m] = []
    depth = {}
    def d(m, stack=()):
        if m in depth:
            return depth[m]
        if m in stack or m not in deps:
            return 0
        depth[m] = 1 + max([d(x, stack + (m,)) for x in deps[m]] or [-1])
        return depth[m]
    for m in deps:
        d(m)
    return depth


def locate_button_method_all(model, method, class_index, seen=None):
    """All (module, file, line) that define `method` along the model and its _inherit parents."""
    seen = seen if seen is not None else set()
    if model in seen:
        return []
    seen.add(model)
    hits = [(rec["module"], rec["file"], rec["methods"][method])
            for rec in class_index.get(model, []) if method in rec["methods"]]
    if hits:
        return hits
    out = []
    for rec in class_index.get(model, []):
        for parent in rec["inherits"]:
            if parent != model:
                out += locate_button_method_all(parent, method, class_index, seen)
    return out


def locate_button_method(model, method, class_index, seen=None):
    """Walk the declared class chain of `model` (and its _inherit parents)
       looking for the SIGNATURE of `method`. Returns (file, line) or None."""
    seen = seen or set()
    if model in seen:
        return None
    seen.add(model)
    for rec in class_index.get(model, []):
        if method in rec["methods"]:
            return rec["file"], rec["methods"][method]
    for rec in class_index.get(model, []):
        for parent in rec["inherits"]:
            if parent != model:
                hit = locate_button_method(parent, method, class_index, seen)
                if hit:
                    return hit
    return None


# ---------------------------------------------------------------- pass 2: server code from XML
REC_RE = re.compile(r'<record\b(.*?)</record>', re.S)
ID_RE = re.compile(r'id="([^"]+)"')
CODE_RE = re.compile(r'<field name="code">(.*?)</field>', re.S)


def scan_server_code(odoo_root, wanted_modules):
    out = {}
    for path in iter_xml(odoo_root):
        mod = module_of(path, odoo_root)
        if mod is None or (wanted_modules and mod not in wanted_modules):
            continue
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "ir.actions.server" not in src:
            continue
        for m in REC_RE.finditer(src):
            block = m.group(1)
            if 'model="ir.actions.server"' not in block:
                continue
            mid, mc = ID_RE.search(block), CODE_RE.search(block)
            if not mid or not mc:
                continue
            xid = mid.group(1)
            key = xid if "." in xid else f"{mod}.{xid}"
            code = mc.group(1).replace("<![CDATA[", "").replace("]]>", "")
            out[key] = dict(module=mod, file=path, code=code.strip())
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--odoo", required=True)
    ap.add_argument("--abox", required=True, nargs="+")
    ap.add_argument("--out-ttl", default="signatures.ttl")
    ap.add_argument("--out-cand", default="candidates.json")
    ap.add_argument("--modules", help="comma list; default = hostModules found in abox")
    args = ap.parse_args()

    g = Graph()
    for f in args.abox:
        g.parse(f, format="turtle")
    q = lambda s: list(g.query("PREFIX : <%s> " % NS + s))

    if args.modules:
        wanted = set(args.modules.split(","))
    else:
        wanted = {str(r[0]).split("#module_")[-1] for r in
                  q("SELECT DISTINCT ?h WHERE { ?m :hostModule ?h . }")}
        wanted |= {str(r[0]).split("#module_")[-1] for r in
                   q("SELECT DISTINCT ?mod WHERE { ?r :inModule ?mod . }")}
        if not wanted:      # abox without inScope markers: fall back to all hosts
            wanted = {str(r[0]).split("#module_")[-1] for r in
                      q("SELECT DISTINCT ?h WHERE { ?m :hostModule ?h . }")}
    print(f"[scope] {len(wanted)} modules")

    class_index, route_defs = scan_python(args.odoo, wanted)
    globals()["DEPTH"] = module_depth(args.odoo)
    print(f"[python] classes for {len(class_index)} models, route defs {len(route_defs)}")

    buttons = [(str(r[0]), str(r[1]), str(r[2])) for r in q(
        "SELECT ?b ?m ?meth WHERE { ?b a :ObjectButton ; :onModel ?m ; "
        ":buttonName ?meth . ?m a :PersistentModel . }")]
    print(f"[abox] buttons on persistent models: {len(buttons)}")

    srv_code = scan_server_code(args.odoo, wanted)
    model_name = {str(r[0]): str(r[1]) for r in q("SELECT ?m ?n WHERE { ?m :modelName ?n . }")}
    host = {str(r[0]): str(r[1]).split("#module_")[-1] for r in q("SELECT ?m ?h WHERE { ?m :hostModule ?h . }")}

    ttl = ["@prefix : <%s> ." % NS, ""]
    cands = []
    used = set()

    def stable(base, line):
        """Content-derived identifier, identical across machines and runs."""
        key = base if base not in used else f"{base}__L{line}"
        used.add(key)
        return key

    # Buttons: attributed to the module that first defines the called method.
    unresolved = 0
    for i, (b_uri, m_uri, meth) in enumerate(buttons):
        mname = model_name.get(m_uri, m_uri.split("#model_")[-1].replace("_", "."))
        hits = locate_button_method_all(mname, meth, class_index)
        if hits:
            mod, f_, l_ = min(hits, key=lambda h: (DEPTH.get(h[0], 0), h[0]))
            ttl.append(f"<{b_uri}> :declaredIn :module_{_slug(mod)} .")
            loc = f"{f_}:{l_}"
        else:
            unresolved += 1
            loc = "UNRESOLVED"
        cands.append(dict(id=f"btn_{i:04d}", kind="button", module=host.get(m_uri, "?"), model=mname,
                          method=meth, locator=loc, uri=b_uri))
    print(f"[buttons] located {len(buttons) - unresolved}, unresolved {unresolved} "
          f"(unresolved buttons stay in the worklist; the AI receives name and model only)")

    # Server actions whose state is "code": the code text is the material for the AI verdict.
    srv_items = [(str(r[0]), str(r[1]), str(r[2])) for r in q(
        'SELECT ?s ?x ?m WHERE { ?s a :ServerAction ; :srvState "code" ; :xmlid ?x ; :actsOn ?m . }')]
    n_code = 0
    for j, (s_uri, xid, m_uri) in enumerate(sorted(srv_items)):
        rec = srv_code.get(xid)
        if rec:
            n_code += 1
            body = rec["code"][:4000].replace('"""', "'''")
            ttl.append(f'<{s_uri}> :srvCodeText """{body}""" .')
        cands.append(dict(id=f"srv_{j:04d}", kind="server", uri=s_uri, xmlid=xid, module=host.get(m_uri, "?"),
                          model=model_name.get(m_uri, "?"), locator=rec["file"] if rec else "XMLID:" + xid))
    print(f"[server] candidates: {len(srv_items)}, code matched: {n_code}")

    # HTTP routes: attributed to the module of the controller.
    for rd in route_defs:
        cid = stable(f"rt_{_slug(rd['module'])}__{_slug(rd['func'])}", rd["line"])
        ttl.append(f":{cid} a :HttpRoute ; :inModule :module_{rd['module']} .")
        cands.append(dict(id=cid, kind="route", uri=NS + "rt_" + _slug(rd["module"]) + "__" + _slug(rd["func"]),
                          module=rd["module"], func=rd["func"], paths=rd["paths"],
                          locator=f'{rd["file"]}:{rd["line"]}'))

    open(args.out_ttl, "w", encoding="utf-8").write("\n".join(ttl) + "\n")
    json.dump(cands, open(args.out_cand, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    print("[out]", dict(Counter(c["kind"] for c in cands)), "->", args.out_ttl, "+", args.out_cand)


if __name__ == "__main__":
    main()
