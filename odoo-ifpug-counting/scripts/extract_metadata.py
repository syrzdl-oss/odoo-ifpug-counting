#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""extract_metadata.py - static extraction of Odoo metadata into the ABox.

Reads the module sources (manifests, model classes, XML data files) of the
counting scope and writes abox.ttl with exactly the facts the counting rules
use: modules, models and fields, views (after inheritance is applied), window,
report and server actions, report templates and object buttons.

Main steps
  1. model registry: model classes, fields, _inherit / _inherits, mixins;
  2. view merging by Odoo semantics (primary/extension inheritance, XPath
     with lxml), QWeb template inheritance;
  3. user visibility of fields (views, including search views, and report
     outputs); delegated (_inherits) fields are injected into the child;
  4. window actions: views provided per type (bindings, view_id,
     <type>_view_ref, default view) and reachability (menu, button, binding);
  5. report templates resolved by QWeb scoping (variables expanded to the
     platform variables docs / res_company, bindings passed along t-call);
  6. subgroups (foldedInto) and fields exposed to EIFs (eifField).

Usage
  python scripts/extract_metadata.py --addons ODOO_ROOT/addons ODOO_ROOT/odoo/addons \\
         --scope-file data/scope.txt --restrict --ttl --out out
Outputs: out/abox.ttl and out/warnings.log
"""
import argparse
import ast
import copy
import csv
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------

FIELD_TYPES = {
    'Char', 'Text', 'Integer', 'Float', 'Boolean', 'Date', 'Datetime',
    'Selection', 'Many2one', 'One2many', 'Many2many', 'Monetary', 'Binary',
    'Html', 'Json', 'Reference', 'Many2oneReference', 'Image', 'Properties',
    'PropertiesDefinition',
}

MODEL_BASES = {'Model': 'model', 'TransientModel': 'transient', 'AbstractModel': 'abstract'}

AUTO_FIELDS = [
    ('id', 'integer', {'store': True}),
    ('create_uid', 'many2one', {'store': True, 'relation': 'res.users'}),
    ('create_date', 'datetime', {'store': True}),
    ('write_uid', 'many2one', {'store': True, 'relation': 'res.users'}),
    ('write_date', 'datetime', {'store': True}),
    ('display_name', 'char', {'store': False, 'compute': '_compute_display_name'}),
]

VIEW_TAGS = {'form', 'tree', 'list', 'kanban', 'calendar', 'search', 'pivot',
             'graph', 'activity', 'cohort', 'gantt', 'map', 'hierarchy'}

SKIP_DIRS = {'tests', 'static', 'demo', 'populate', 'i18n', 'doc'}

LITERAL_TRUE = {'1', 'True', 'true'}


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------

class Warnings:

    def __init__(self):
        self.items = []
        self._once = set()

    def add(self, code, msg, model=None, view=None, field=None, module=None):
        self.items.append((code, msg, model, view, field, module))

    def once(self, code, msg, **kw):
        if code not in self._once:
            self._once.add(code)
            self.add(code, msg, **kw)

    def dump(self, path):
        with open(path, 'w', encoding='utf-8') as f:
            for it in self.items:
                f.write(f'[{it[0]}] {it[1]}\n')

def ttl_escape(s):
    return str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')


WARN = Warnings()


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------

MANIFEST_INFO = {}      # module -> {'application': bool, 'depends': [str]}


def discover_modules(addons_paths):
    mods = {}
    for root in addons_paths:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            WARN.add('PATH', f'addons path does not exist: {root}')
            continue
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, '__manifest__.py')):
                if name in mods:
                    WARN.add('DUPMOD', f'duplicate module name, first one kept: {name}')
                else:
                    mods[name] = p
    return mods


def parse_manifest(path):
    src = open(path, encoding='utf-8', errors='ignore').read()
    try:
        data = ast.literal_eval(src)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    m = re.search(r"['\"]depends['\"]\s*:\s*\[(.*?)\]", src, re.S)
    deps = re.findall(r"['\"]([\w\.]+)['\"]", m.group(1)) if m else []
    WARN.add('MANIFEST', f'literal_eval failed, regex fallback: {path}')
    return {'depends': deps}


def topo_order(modules, depends):
    present = set(modules)
    indeg = {m: 0 for m in present}
    rev = defaultdict(list)                      # dep -> [dependants]
    for m in present:
        for d in depends.get(m, []):
            if d not in present:
                WARN.add('MISSDEP', f'{m} depends on {d}, which is missing (its fields will be absent)',
                         module=m)
                continue
            indeg[m] += 1
            rev[d].append(m)
    order, queue = [], sorted([m for m in present if indeg[m] == 0])
    while queue:
        n = queue.pop(0)
        order.append(n)
        changed = False
        for m in sorted(rev[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
                changed = True
        if changed:
            queue.sort()
    if len(order) != len(present):
        rest = sorted(present - set(order))
        WARN.add('CYCLE', f'dependency graph has a cycle or dangling edge; rest appended by name: {rest}')
        order += rest
    return order


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------

def const(node):
    if isinstance(node, ast.Constant):
        return node.value
    return '<dynamic>'


def parse_field_call(call):
    fn = call.func
    tname = None
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
            and fn.value.id == 'fields' and fn.attr in FIELD_TYPES:
        tname = fn.attr
    elif isinstance(fn, ast.Name) and fn.id in FIELD_TYPES:   # from odoo.fields import Char
        tname = fn.id
    if tname is None:
        return None
    pos = [const(a) for a in call.args]
    kw = {}
    for k in call.keywords:
        if k.arg is None:
            kw['**'] = '<dynamic>'
        else:
            kw[k.arg] = const(k.value)
    return tname.lower(), pos, kw


def collect_module_declarations(module, path):
    decls = []
    pyfiles = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
        for fn in filenames:
            if fn.endswith('.py') and fn not in ('__manifest__.py',):
                pyfiles.append(os.path.join(dirpath, fn))
    pyfiles.sort()
    class_kind = {}
    for fp in pyfiles:
        try:
            tree = ast.parse(open(fp, encoding='utf-8', errors='ignore').read())
        except SyntaxError as e:
            WARN.add('PYPARSE', f'{fp}: {e}')
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            kind = None
            for b in node.bases:
                if isinstance(b, ast.Attribute) and b.attr in MODEL_BASES:
                    kind = MODEL_BASES[b.attr]
                elif isinstance(b, ast.Name) and b.id in MODEL_BASES:
                    kind = MODEL_BASES[b.id]
                elif isinstance(b, ast.Name) and b.id in class_kind:
                    kind = class_kind[b.id]
                elif isinstance(b, ast.Attribute) and b.attr in class_kind:
                    kind = class_kind[b.attr]
            if kind is None:
                continue
            class_kind[node.name] = kind
            d = {'module': module, 'cls': node.name, 'file': fp, 'kind': kind,
                 'name': None, 'inherit': [], 'inherits': {}, 'auto': None,
                 'log_access': None, 'fields': {}}
            for st in node.body:
                if isinstance(st, ast.Assign) and len(st.targets) == 1:
                    tgt, val = st.targets[0], st.value
                elif isinstance(st, ast.AnnAssign) and st.value is not None:
                    tgt, val = st.target, st.value
                else:
                    continue
                if not isinstance(tgt, ast.Name):
                    continue
                tn = tgt.id
                if tn == '_name':
                    v = const(val)
                    d['name'] = v if isinstance(v, str) else None
                    if d['name'] is None:
                        WARN.add('DYNNAME', f'{fp}:{node.name} _name is not a literal')
                elif tn == '_inherit':
                    if isinstance(val, (ast.List, ast.Tuple)):
                        d['inherit'] = [const(e) for e in val.elts]
                    else:
                        v = const(val)
                        d['inherit'] = [v] if isinstance(v, str) else []
                        if v == '<dynamic>':
                            WARN.add('DYNINH', f'{fp}:{node.name} _inherit is dynamic')
                elif tn == '_inherits':
                    if isinstance(val, ast.Dict):
                        d['inherits'] = {const(k): const(v)
                                         for k, v in zip(val.keys, val.values)}
                elif tn == '_auto':
                    v = const(val)
                    d['auto'] = v if isinstance(v, bool) else None
                elif tn == '_log_access':
                    v = const(val)
                    d['log_access'] = v if isinstance(v, bool) else None
                elif isinstance(val, ast.Call):
                    pf = parse_field_call(val)
                    if pf:
                        ttype, pos, kw = pf
                        d['fields'][tn] = {'ttype': ttype, 'pos': pos, 'kw': kw}
            decls.append(d)
    return decls


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------

def new_field_entry(name, ttype, pos, kw, module, via):
    return {'name': name, 'ttype': ttype, 'pos': list(pos), 'kw': dict(kw),
            'contributed_by': module,
            'via': via,
            'overridden_by': []}


def merge_field(old, new, module):
    old['ttype'] = new['ttype']
    if new['pos']:
        old['pos'] = list(new['pos'])
    old['kw'].update(new['kw'])
    old['overridden_by'].append(module)


def resolve_registry(order, module_decls):
    registry = {}

    def ensure(model, kind, auto, module):
        if model not in registry:
            registry[model] = {'kind': kind, 'auto': auto, 'host': module,
                               'log_access': None, 'contributing': set(),
                               'fields': {}, 'mixins': [], 'delegates': {}}
        return registry[model]

    pending = [(m, d) for m in order for d in module_decls.get(m, [])]
    while pending:
        deferred, progressed = [], False
        for module, d in pending:
            name, inh = d['name'], [x for x in d['inherit'] if isinstance(x, str)]

            if name:
                if name in registry:
                    entry = registry[name]
                else:
                    entry = ensure(name, d['kind'], d['auto'], module)
                if d['auto'] is not None:
                    entry['auto'] = d['auto']
                for mx in inh:
                    if mx == name:
                        continue
                    entry['mixins'].append((mx, module))
                if d['inherits']:
                    entry['delegates'].update({k: v for k, v in d['inherits'].items()
                                               if isinstance(k, str)})
                target = entry

            elif len(inh) >= 1:
                tgt_name = inh[0]
                if len(inh) > 1:
                    WARN.add('MULTIEXT', f'{module}:{d["cls"]} has no _name and several _inherit targets; first one taken')
                if tgt_name not in registry:
                    deferred.append((module, d))
                    continue
                target = registry[tgt_name]
                if d['inherits']:
                    target['delegates'].update({k: v for k, v in d['inherits'].items()
                                                if isinstance(k, str)})
            else:
                continue

            progressed = True
            if d.get('log_access') is not None:
                target['log_access'] = d['log_access']
            target['contributing'].add(module)
            for fn, fd in d['fields'].items():
                if fn in target['fields']:
                    merge_field(target['fields'][fn],
                                new_field_entry(fn, fd['ttype'], fd['pos'], fd['kw'],
                                                module, 'own'), module)
                else:
                    target['fields'][fn] = new_field_entry(
                        fn, fd['ttype'], fd['pos'], fd['kw'], module, 'own')
        if not progressed:
            for module, d in deferred:
                WARN.add('EXTMISS', f'{module} extends {d["inherit"]}, whose definition is missing',
                         module=module)
            break
        pending = deferred

    changed = True
    while changed:
        changed = False
        for model, entry in registry.items():
            for mx, _via_mod in entry['mixins']:
                src = registry.get(mx)
                if src is None:
                    continue
                for fn, fe in src['fields'].items():
                    if fe['via'] == 'injected' or fn in entry['fields']:
                        continue
                    e = copy.deepcopy(fe)
                    e['via'] = f'mixin:{mx}'
                    e['mixin_applied_by'] = _via_mod
                    entry['fields'][fn] = e
                    changed = True
                for tgt, fk in src['delegates'].items():
                    if tgt not in entry['delegates']:
                        entry['delegates'][tgt] = fk
                        changed = True
    for model, entry in registry.items():
        for mx, _ in entry['mixins']:
            if mx not in registry:
                WARN.add('MIXMISS', f'{model}: mixin {mx} not resolved (module missing); its fields are absent',
                         model=model)
    return registry


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------

def inject_auto_fields(registry):
    for model, ent in registry.items():
        la = ent.get('log_access')
        quartet_ok = (ent['kind'] != 'abstract'
                      and (la is True or (la is None and ent['auto'] is not False)))
        for fname, ttype, extra in AUTO_FIELDS:
            if fname in ent['fields']:
                continue
            if fname not in ('id', 'display_name') and not quartet_ok:
                continue
            kw = {}
            if 'compute' in extra:
                kw['compute'] = extra['compute']
            kw['store'] = extra.get('store', True)
            pos = [extra['relation']] if 'relation' in extra else []
            ent['fields'][fname] = new_field_entry(fname, ttype, pos, kw,
                                                   ent['host'], 'injected')


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------

def finalize_field(model, fe):
    kw, pos, ttype = fe['kw'], fe['pos'], fe['ttype']

    relation = None
    if ttype in ('many2one', 'many2oneReference'.lower()):
        relation = kw.get('comodel_name') or (pos[0] if pos and isinstance(pos[0], str) else None)
    elif ttype == 'one2many':
        relation = kw.get('comodel_name') or (pos[0] if pos and isinstance(pos[0], str) else None)
        fe['inverse_name'] = kw.get('inverse_name') or \
            (pos[1] if len(pos) > 1 and isinstance(pos[1], str) else None)
    elif ttype == 'many2many':
        relation = kw.get('comodel_name') or (pos[0] if pos and isinstance(pos[0], str) else None)
    if relation == '<dynamic>':
        relation = None
        WARN.add('DYNREL', f'{model}.{fe["name"]} relation target is dynamic',
                 model=model, field=fe["name"])
    fe['relation'] = relation

    comp = kw.get('compute')
    fe['is_computed'] = comp is not None
    fe['compute'] = comp if isinstance(comp, str) else ('<dynamic>' if comp is not None else '')
    rel = kw.get('related')
    fe['is_related'] = rel is not None
    fe['related'] = rel if isinstance(rel, str) else ('<dynamic>' if rel is not None else '')

    st = kw.get('store', None)
    if isinstance(st, bool):
        fe['store'] = st
    elif st == '<dynamic>':
        fe['store'] = not (fe['is_computed'] or fe['is_related'])
        WARN.add('DYNSTORE', f'{model}.{fe["name"]} store is dynamic; default rule applied')
    else:
        fe['store'] = not (fe['is_computed'] or fe['is_related'])

    req = kw.get('required')
    fe['required'] = req if isinstance(req, bool) else ''
    od = kw.get('ondelete')
    fe['ondelete'] = od if isinstance(od, str) else ''
    fe['is_auto'] = (fe['via'] == 'injected')
    if kw.get('selection') == '<dynamic>' or (pos and pos[0] == '<dynamic>' and ttype == 'selection'):
        WARN.add('DYNSEL', f'{model}.{fe["name"]} selection is given by a method')


def _id_hidden(expr):
    import ast as _ast
    if not expr or 'id' not in expr:
        return (False, False)
    try:
        tree = _ast.parse(expr.strip(), mode='eval')
    except SyntaxError:
        return (False, False)

    def ev(n, idv):
        if isinstance(n, _ast.Name):
            return idv if n.id == 'id' else None
        if isinstance(n, _ast.Constant):
            return bool(n.value)
        if isinstance(n, _ast.UnaryOp) and isinstance(n.op, _ast.Not):
            v = ev(n.operand, idv)
            return None if v is None else (not v)
        if isinstance(n, _ast.BoolOp):
            vals = [ev(x, idv) for x in n.values]
            if isinstance(n.op, _ast.Or):
                return True if any(v is True for v in vals) else (False if all(v is False for v in vals) else None)
            return False if any(v is False for v in vals) else (True if all(v is True for v in vals) else None)
        return None
    return (ev(tree.body, False) is True, ev(tree.body, True) is True)


def inject_delegated_fields(registry):
    added, changed = 0, True
    while changed:
        changed = False
        for model, ent in registry.items():
            for parent, fk in (ent.get('delegates') or {}).items():
                pent = registry.get(parent)
                if not pent or not fk:
                    continue
                for fname, pfe in pent['fields'].items():
                    if fname in ent['fields'] or pfe.get('via') == 'injected':
                        continue
                    nfe = copy.deepcopy(pfe)
                    kw = {k: v for k, v in nfe['kw'].items() if k not in ('compute', 'store', 'related', 'inverse')}
                    kw['related'] = f'{fk}.{fname}'
                    nfe['kw'] = kw
                    nfe['via'] = 'delegated'
                    nfe['delegated_from'] = parent
                    ent['fields'][fname] = nfe
                    added += 1
                    changed = True
    print(f'delegated (_inherits) fields injected: {added}')
    return added


def propagate_delegated_visibility(registry):
    n = 0
    for model, ent in registry.items():
        for fname, fe in ent['fields'].items():
            par = fe.get('delegated_from')
            if par and fe.get('is_user_visible'):
                pfe = (registry.get(par) or {}).get('fields', {}).get(fname)
                if pfe is not None and not pfe.get('is_user_visible'):
                    pfe['is_user_visible'] = True
                    pfe['view_types'] = ','.join(sorted((set((pfe.get('view_types') or '').split(',')) | {'delegated'}) - {''}))
                    n += 1
    return n


def finalize_registry(registry):
    for model, ent in registry.items():
        for fe in ent['fields'].values():
            finalize_field(model, fe)


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------

def iter_view_records(module, path):
    """yield (full_id, model, inherit_ref_or_None, arch_element, mode, priority)"""
    xmls = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
        for fn in filenames:
            if fn.endswith('.xml'):
                xmls.append(os.path.join(dirpath, fn))
    xmls.sort()                                               # A1
    for fp in xmls:
        try:
            root = ET.parse(fp).getroot()
        except ET.ParseError as e:
            WARN.add('XMLPARSE', f'{fp}: {e}')
            continue
        for rec in root.iter('record'):
            if rec.get('model') != 'ir.ui.view':
                continue
            rid = rec.get('id') or f'__anon__{module}_{len(xmls)}'
            full_id = rid if '.' in rid else f'{module}.{rid}'
            model = inherit = None
            arch = None
            mode = 'extension'
            prio = 16
            active = True
            for f in rec.findall('field'):
                nm = f.get('name')
                if nm == 'model':
                    model = (f.text or '').strip()
                elif nm == 'inherit_id':
                    inherit = f.get('ref')
                    if inherit and '.' not in inherit:
                        inherit = f'{module}.{inherit}'
                elif nm == 'mode':
                    mode = (f.text or '').strip() or mode
                elif nm == 'priority':
                    try:
                        prio = int((f.text or '16').strip())
                    except ValueError:
                        pass
                elif nm == 'arch':
                    arch = f
                elif nm == 'active':
                    _av = (f.get('eval') or f.text or '').strip()
                    if _av in ('False', 'false', '0'):
                        active = False
            if arch is None:
                continue
            yield full_id, model, inherit, arch, mode, prio, active


def _unwrap_arch(arch_field_elem):
    kids = list(arch_field_elem)
    if len(kids) == 1 and kids[0].tag == 'data':
        return kids[0]
    wrapper = ET.Element('data')
    for k in kids:
        wrapper.append(k)
    return wrapper


try:
    import lxml.etree as _LX

    def _hasclass(context, *cls):
        return set(context.context_node.attrib.get('class', '').split()).issuperset(cls)
    _LX_NS = _LX.FunctionNamespace(None)
    _LX_NS['hasclass'] = _hasclass
except ImportError:
    _LX = None


def _lxml_targets(base, expr):
    if _LX is None:
        WARN.once('NOLXML', 'lxml not installed: XPath with hasclass()/indexes/contains() cannot be evaluated (pip install lxml)')
        return []
    try:
        lroot = _LX.fromstring(ET.tostring(base))
        hits = lroot.xpath(expr)
    except Exception:
        return []
    out = []
    for h in hits if isinstance(hits, list) else []:
        if not isinstance(getattr(h, 'tag', None), str):
            continue
        path, n = [], h
        while n is not lroot and n is not None:
            par = n.getparent()
            if par is None:
                break
            path.append([c for c in par if isinstance(c.tag, str)].index(n))
            n = par
        node = base
        for i in reversed(path):
            kids = list(node)
            if i >= len(kids):
                node = None
                break
            node = kids[i]
        if node is not None:
            out.append(node)
    return out


def _find_targets(base, expr):
    e = expr.strip()
    if e.startswith('//'):
        e = '.' + e
    elif not e.startswith('.'):
        e = './/' + e.lstrip('/')
    try:
        found = base.findall(e)
    except SyntaxError:
        found = []
    return found or _lxml_targets(base, expr.strip())


def apply_patch(base, patch_arch, view_id):
    parent_of = {}

    def rebuild():
        parent_of.clear()
        for p in base.iter():
            for c in p:
                parent_of[c] = p

    def locate(spec):
        if spec.tag == 'xpath':
            t = _find_targets(base, spec.get('expr', ''))
        else:                                                 # <field name="x" position=...>
            cond = ''.join(f'[@{k}="{v}"]' for k, v in spec.attrib.items()
                           if k not in ('position',))
            try:
                t = base.findall(f'.//{spec.tag}{cond}')
            except SyntaxError:
                t = []
            if not t:
                _c = ''.join("[@%s=%s]" % (k, repr(v) if "'" not in v else '"%s"' % v)
                             for k, v in spec.attrib.items() if k != 'position')
                t = _lxml_targets(base, f'//{spec.tag}{_c}')
        return t[0] if t else None

    def one(spec):
        pos = spec.get('position', 'inside')
        tgt = locate(spec)
        if tgt is None:
            WARN.add('PATCHMISS',
                     f'{view_id}: patch target not found <{spec.tag} '
                     f'{spec.attrib}>, skipped',
                     view=view_id)
            return
        rebuild()
        par = parent_of.get(tgt)
        payload = [c for c in spec if not (spec.tag != 'xpath' and False)]
        if pos == 'attributes':
            for attr in spec.findall('attribute'):
                an = attr.get('name')
                if an is None:
                    continue
                if attr.text is None or attr.text == '':
                    tgt.attrib.pop(an, None)
                else:
                    tgt.set(an, attr.text)
        elif pos == 'inside':
            for c in payload:
                tgt.append(copy.deepcopy(c))
        elif pos in ('after', 'before'):
            if par is None:
                WARN.add('PATCHROOT', f'{view_id}: after/before targets the root, skipped')
                return
            idx = list(par).index(tgt) + (1 if pos == 'after' else 0)
            for c in payload:
                par.insert(idx, copy.deepcopy(c))
                idx += 1
        elif pos == 'replace':
            if par is None:
                WARN.add('PATCHROOT', f'{view_id}: replace of the root, skipped')
                return
            idx = list(par).index(tgt)
            par.remove(tgt)
            for c in payload:
                par.insert(idx, copy.deepcopy(c))
                idx += 1
        else:
            WARN.add('PATCHPOS', f'{view_id}: unknown position={pos}, skipped')

    def walk(container):
        for child in list(container):
            if child.tag == 'data':
                walk(child)
            elif child.tag == 'xpath' or child.get('position'):
                one(child)
            else:
                WARN.add('PATCHTOP', f'{view_id}: non-patch element at the top of an inheriting arch '
                                     f'<{child.tag}>, ignored')
    walk(patch_arch)


_XREF = re.compile(r'%\(([\w.]+)\)d')


def _canon_refs(elem, module):
    fix = lambda m: '%%(%s)d' % (m.group(1) if '.' in m.group(1) else f'{module}.{m.group(1)}')
    for el in elem.iter():
        for k, v in list(el.attrib.items()):
            if '%(' in v:
                el.set(k, _XREF.sub(fix, v))
    return elem


def merge_views(order, modules):
    recs, idx = {}, 0
    for module in order:
        for vid, model, inherit, arch_f, mode, prio, active in iter_view_records(module, modules[module]):
            if not active:
                WARN.add('VIEWINACTIVE', f'{vid}: active=False, not merged', view=vid)
                continue
            if vid in recs:
                WARN.add('VIEWREDEF', f'{vid}: redeclared by {module}', view=vid)
            recs[vid] = {'vid': vid, 'module': module, 'model': model, 'inherit': inherit,
                         'arch': _canon_refs(_unwrap_arch(arch_f), module), 'mode': mode, 'prio': prio, 'idx': idx}
            idx += 1
    children = defaultdict(list)
    for r in recs.values():
        if r['inherit']:
            children[r['inherit']].append(r)
    for lst in children.values():
        lst.sort(key=lambda r: (r['prio'], r['idx']))

    def is_full(r):
        return r['inherit'] is None or r['mode'] == 'primary'

    def owner(vid, seen=()):
        r = recs.get(vid)
        if r is None or vid in seen:
            return None
        return vid if is_full(r) else owner(r['inherit'], seen + (vid,))

    memo, busy = {}, set()

    def apply_children(vid, arch, patched):
        for c in children.get(vid, []):
            if c['mode'] == 'primary':
                continue
            apply_patch(arch, c['arch'], c['vid'])
            if c['module'] not in patched:
                patched.append(c['module'])
            apply_children(c['vid'], arch, patched)

    def combined(vid):
        if vid in memo:
            return memo[vid]
        if vid in busy:
            WARN.add('VIEWCYCLE', f'{vid}: inheritance cycle, skipped', view=vid)
            return None
        busy.add(vid)
        r = recs[vid]
        patched = []
        if r['inherit'] is None:
            arch, model = copy.deepcopy(r['arch']), r['model']
        else:
            po = owner(r['inherit'])
            parent = combined(po) if po else None
            if parent is None:
                WARN.add('VIEWMISS', f'{vid}: parent view {r["inherit"]} missing, skipped', view=vid)
                busy.discard(vid)
                memo[vid] = None
                return None
            arch = copy.deepcopy(parent['arch'])
            model = r['model'] or parent['model']
            apply_patch(arch, r['arch'], vid)
            patched.append(r['module'])
        apply_children(vid, arch, patched)
        memo[vid] = {'model': model, 'arch': arch, 'priority': r['prio'],
                     'module': r['module'], 'patchedBy': patched}
        busy.discard(vid)
        return memo[vid]

    merged = {}
    for vid, r in recs.items():
        if is_full(r):
            v = combined(vid)
            if v is not None:
                merged[vid] = v
    for vid, r in recs.items():
        if not is_full(r) and owner(vid) is None:
            WARN.add('VIEWMISS', f'{vid}: parent view {r["inherit"]} missing, skipped', view=vid)
    return merged


def scan_visibility(merged, registry):
    seen = defaultdict(lambda: {'visible': False, 'views': set()})   # (model,field) ->

    def relation_of(model, fname):
        ent = registry.get(model)
        if ent and fname in ent['fields']:
            return ent['fields'][fname].get('relation')
        return None

    def scan(elem, model, vtype):
        for child in elem:
            tag = child.tag
            if tag == 'field' and child.get('name'):
                fn = child.get('name')
                inv = child.get('invisible')
                cinv = child.get('column_invisible')
                literally_hidden = (inv in LITERAL_TRUE) or (cinv in LITERAL_TRUE)
                if child.get('groups'):
                    WARN.once('GROUPS', 'fields restricted by groups= are treated as visible')
                rec = seen[(model, fn)]
                rec['views'].add(vtype)
                if not literally_hidden:
                    rec['visible'] = True
                subviews = [c for c in child if c.tag in VIEW_TAGS]
                if subviews:
                    sub_model = relation_of(model, fn) or model
                    for sv in subviews:
                        scan(sv, sub_model, sv.tag)
            else:
                scan(child, model, vtype)

    for vid, v in merged.items():
        model = v['model']
        if not model:
            continue
        arch = v['arch']
        tops = [c for c in arch.iter() if c.tag in VIEW_TAGS]
        direct = [c for c in arch if c.tag in VIEW_TAGS] or tops[:1]
        for t in direct:
            scan(t, model, t.tag)

    for (model, fn), rec in seen.items():
        ent = registry.get(model)
        if ent and fn in ent['fields']:
            fe = ent['fields'][fn]
            fe['is_user_visible'] = rec['visible']
            fe['view_types'] = ','.join(sorted(rec['views']))
        elif ent is not None:
            WARN.add('VIEWFIELD', f'view references unknown field {model}.{fn}')

    for ent in registry.values():
        for fe in ent['fields'].values():
            fe.setdefault('is_user_visible', False)
            fe.setdefault('view_types', '')


# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------

def model_kind(ent):
    if ent['kind'] == 'abstract':
        return 'abstract'
    if ent['kind'] == 'transient':
        return 'transient'
    return 'persistent'


def sanitize(s):
    return re.sub(r'[^0-9a-zA-Z_]', '_', s)




TTL_HEADER = """@prefix : <http://example.org/odoo-ifpug#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
# ABox generated by extract_metadata.py (closed world: boolean properties asserted per individual)
"""

def ttl_bool(b):
    return f'"{str(bool(b)).lower()}"^^xsd:boolean'


def compute_folding_and_eif(registry, acts, scope):
    has_door = set()
    for a in acts['window'].values():
        if a.get('reach_kind') != 'none' and a.get('res_model'):
            has_door.add(a['res_model'])

    candidates = defaultdict(list)   # child -> [(source_rank, parent), ...]
    for model, ent in registry.items():
        for tgt, _fk in ent.get('delegates', {}).items():
            tent = registry.get(tgt)
            if tent and model_kind(tent) == 'persistent' and tgt not in has_door:
                candidates[tgt].append((0, model))
        for fn, fe in ent['fields'].items():
            if fe.get('via') == 'delegated':
                continue
            if fe.get('ttype') != 'one2many':
                continue
            child = fe.get('relation')
            if not child or child not in registry or model_kind(registry[child]) != 'persistent':
                continue
            if child in has_door:
                continue
            inv = fe.get('inverse_name')
            if not inv:
                continue
            cfe = registry[child]['fields'].get(inv)
            if cfe and cfe.get('required') is True and cfe.get('ondelete') == 'cascade':
                candidates[child].append((1, model))
    folded = {}
    multi_parent_log = []
    for child, cands in candidates.items():
        cands_sorted = sorted(set(cands), key=lambda x: (x[0], x[1]))
        folded[child] = cands_sorted[0][1]
        if len(set(p for _, p in cands)) > 1:
            multi_parent_log.append((child, [p for _, p in cands_sorted], folded[child]))
    if multi_parent_log:
        print(f'[subgroups] {len(multi_parent_log)} child tables qualify for several parents; resolved by'
              f' "delegation > one2many, then alphabetical":')
        for child, parents, winner in multi_parent_log:
            print(f'     {child} <- candidates {parents}, folded into {winner}')

    eif_exposed = defaultdict(set)
    for model, ent in registry.items():
        if ent['host'] not in scope or model_kind(ent) != 'persistent':
            continue
        for fn, fe in ent['fields'].items():
            if fe.get('via') == 'delegated':
                continue
            path = fe.get('related')
            if not path or path in ('', '<dynamic>') or not fe.get('is_user_visible'):
                continue
            cur = model
            segs = path.split('.')
            for k, seg in enumerate(segs):
                cfe = registry.get(cur, {}).get('fields', {}).get(seg)
                if cfe is None:
                    break
                if k < len(segs) - 1:
                    cur = cfe.get('relation')
                    if not cur:
                        break
                else:
                    tent = registry.get(cur)
                    if (tent and model_kind(tent) == 'persistent'
                            and tent['host'] != ent['host']):
                        eif_exposed[model].add((seg, cur))
    return folded, eif_exposed


def emit_ttl(registry, scope, outdir, acl=None):
    path = os.path.join(outdir, 'abox.ttl')
    kinds = {'persistent': ':PersistentModel', 'transient': ':TransientModel',
             'abstract': ':AbstractModel'}
    with open(path, 'w', encoding='utf-8') as f:
        f.write(TTL_HEADER)
        mods = sorted({e['host'] for e in registry.values()} |
                      {m for e in registry.values() for m in e['contributing']})
        for m in mods:
            _mi = MANIFEST_INFO.get(m, {})
            registry_modules = MANIFEST_INFO.keys()
            f.write(f':module_{sanitize(m)} a :Module ;\n')
            f.write(f'    :isApplication {ttl_bool(_mi.get("application", False))} ')
            f.write('.\n')
        for model in sorted(registry):
            e = registry[model]
            mid = f':model_{sanitize(model)}'
            f.write(f'\n{mid} a {kinds[model_kind(e)]} ;\n')
            f.write(f'    :modelName "{model}" ;\n')
            f.write(f'    :hostModule :module_{sanitize(e["host"])} .\n')
        for model in sorted(registry):
            e = registry[model]
            mid = f':model_{sanitize(model)}'
            for fn in e['fields']:
                fe = e['fields'][fn]
                fid = f':field_{sanitize(model)}__{sanitize(fn)}'
                f.write(f'\n{fid} a :OdooField ;\n')
                f.write(f'    :fieldName "{fn}" ;\n')
                f.write(f'    :belongsTo {mid} ;\n')
                f.write(f'    :ttype "{fe["ttype"]}" ;\n')
                if fe.get('relation'):
                    f.write(f'    :relatesTo :model_{sanitize(fe["relation"])} ;\n')
                f.write(f'    :store {ttl_bool(fe["store"])} ;\n')
                f.write(f'    :isComputed {ttl_bool(fe["is_computed"])} ;\n')
                f.write(f'    :isRelated {ttl_bool(fe["is_related"])} ;\n')
                f.write(f'    :isUserVisible {ttl_bool(fe["is_user_visible"])} .\n')
    return path




# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------

def _full_id(module, rid):
    return rid if '.' in rid else f'{module}.{rid}'


def _fields_of_record(rec):
    d = {}
    for f in rec.findall('field'):
        nm = f.get('name')
        if nm:
            d[nm] = f
    return d


AW_VIEW_BINDINGS = {}


def _inline_view_bindings(ev, module):
    out = {}
    for m in re.finditer(r"\{([^{}]*)\}", ev or ''):
        body = m.group(1)
        vm = re.search(r"['\"]view_mode['\"]\s*:\s*['\"](\w+)['\"]", body)
        vi = re.search(r"['\"]view_id['\"]\s*:\s*ref\(\s*['\"]([\w.]+)['\"]\s*\)", body)
        if vm and vi:
            out[vm.group(1)] = _full_id(module, vi.group(1))
    return out


def _context_view_ref(ctx, vtype, module):
    keys = [f'{vtype}_view_ref'] + (['tree_view_ref'] if vtype == 'list' else [])
    for k in keys:
        m = re.search(r"['\"]%s['\"]\s*:\s*['\"]([\w.]+)['\"]" % k, ctx or '')
        if m:
            return _full_id(module, m.group(1))
    return None


_RECORDSET_SAME = {'with_context', 'sudo', 'with_company', 'with_user', 'with_env',
                   'filtered', 'filtered_domain', 'sorted', 'exists', 'ensure_one'}
PLATFORM_VARS = {'docs': None, 'res_company': 'res.company'}


def _chain_of(node, local=None, seen=None):
    import ast as _ast
    seen = seen or set()
    if isinstance(node, _ast.Name):
        if local and node.id in local and node.id not in seen:
            return _chain_of(local[node.id], local, seen | {node.id})
        return node.id
    if isinstance(node, _ast.Attribute):
        base = _chain_of(node.value, local, seen)
        return f'{base}.{node.attr}' if base else None
    if isinstance(node, _ast.Subscript):
        return _chain_of(node.value, local, seen)
    if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute):
        if node.func.attr in _RECORDSET_SAME:
            return _chain_of(node.func.value, local, seen)
        if (node.func.attr == 'mapped' and node.args and isinstance(node.args[0], _ast.Constant)
                and isinstance(node.args[0].value, str)):
            base = _chain_of(node.func.value, local, seen)
            return f'{base}.{node.args[0].value}' if base else None
    return None


def _attr_chains(expr):
    import ast as _ast
    try:
        tree = _ast.parse(expr.strip(), mode='eval')
    except SyntaxError:
        return {expr.strip()} if re.fullmatch(r'[\w.]+', expr.strip()) else set()
    local = {}
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.GeneratorExp, _ast.ListComp, _ast.SetComp, _ast.DictComp)):
            for gen in node.generators:
                if isinstance(gen.target, _ast.Name):
                    local[gen.target.id] = gen.iter
    out = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Attribute, _ast.Call)):
            ch = _chain_of(node, local)
            if ch and '.' in ch:
                out.add(ch)
    return out


def _value_chain(expr):
    import ast as _ast
    try:
        return _chain_of(_ast.parse((expr or '').strip(), mode='eval').body)
    except SyntaxError:
        return None


def _subst(chain, env):
    head, _, rest = chain.partition('.')
    if head in env:
        if env[head] is None:
            return None
        return env[head] + ('.' + rest if rest else '')
    return chain


def _template_stats(arch):
    outputs, calls, paths, tcalls = [], [], [], []
    n_tf = n_te = 0
    loopvar = {}

    def bind(env, var, expr):
        ch = _value_chain(expr)
        env[var] = _subst(ch, env) if ch else None

    def walk(el, env):
        nonlocal n_tf, n_te
        local = dict(env)
        for child in el:
            if not isinstance(child.tag, str):
                continue
            cenv = local
            if child.get('t-foreach') and child.get('t-as'):
                cenv = dict(local)
                bind(cenv, child.get('t-as').strip(), child.get('t-foreach'))
                loopvar[child.get('t-as').strip()] = child.get('t-foreach').strip()
            v = child.get('t-field')
            if v:
                n_tf += 1
                ch = [c for c in (_subst(x, cenv) for x in _attr_chains(v)) if c]
                outputs.append((re.sub(r'\s+', '', v), ch))
                paths.extend(ch)
            for k in ('t-esc', 't-out'):
                w = child.get(k)
                if w:
                    n_te += 1
                    ch = [c for c in (_subst(x, cenv) for x in _attr_chains(w)) if c]
                    outputs.append((re.sub(r'\s+', '', w), ch))
                    paths.extend(ch)
            tc = (child.get('t-call') or '').strip()
            if tc and re.fullmatch(r'[\w.]+', tc):
                tcalls.append(tc)
                callenv = dict(cenv)
                for g in child:
                    if isinstance(g.tag, str) and g.get('t-set') and g.get('t-value'):
                        bind(callenv, g.get('t-set').strip(), g.get('t-value'))
                calls.append((tc, callenv))
            if child.get('t-set') and child.get('t-value'):
                bind(local, child.get('t-set').strip(), child.get('t-value'))
            walk(child, cenv)

    walk(arch, {})
    return {'n_tfield': n_tf, 'n_tesc': n_te, 'paths': paths, 'loopvars': loopvar, 'tcalls': tcalls,
            'outputs': outputs, 'calls': calls}


def _combine_templates(tpl_recs):
    children = defaultdict(list)
    for fid, r in tpl_recs.items():
        if r['inherit']:
            children[r['inherit']].append((fid, r))
    for lst in children.values():
        lst.sort(key=lambda x: (x[1]['prio'], x[1]['idx']))

    def patch_of(elem):
        w = ET.Element('data')
        for c in elem:
            w.append(copy.deepcopy(c))
        return w

    def is_full(r):
        return r['inherit'] is None or r['primary']

    def owner(fid, seen=()):
        r = tpl_recs.get(fid)
        if r is None or fid in seen:
            return None
        return fid if is_full(r) else owner(r['inherit'], seen + (fid,))

    memo, busy = {}, set()

    def apply_children(fid, arch):
        for cid, c in children.get(fid, []):
            if c['primary']:
                continue
            apply_patch(arch, patch_of(c['elem']), cid)
            apply_children(cid, arch)

    def combined(fid):
        if fid in memo:
            return memo[fid]
        if fid in busy:
            return None
        busy.add(fid)
        r = tpl_recs[fid]
        if r['inherit'] is None:
            arch = copy.deepcopy(r['elem'])
        else:
            po = owner(r['inherit'])
            parent = combined(po) if po else None
            if parent is None:
                WARN.add('TPLMISS', f'{fid}: parent template {r["inherit"]} missing, treated as standalone')
                arch = copy.deepcopy(r['elem'])
            else:
                arch = copy.deepcopy(parent)
                apply_patch(arch, patch_of(r['elem']), fid)
        apply_children(fid, arch)
        memo[fid] = arch
        busy.discard(fid)
        return arch

    out = {}
    for fid, r in tpl_recs.items():
        if is_full(r):
            arch = combined(fid)
            if arch is not None:
                out[fid] = dict(module=r['module'], **_template_stats(arch))
    return out


def collect_actions(order, modules):
    win, rep, srv, cron, wmenu = {}, {}, {}, {}, {}
    menu_targets = set()
    templates = {}                # full_id -> {'module','n_tfield','n_tesc','paths':[..]}
    tpl_recs = {}
    for module in order:
        path = modules[module]
        xmls = []
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith('.')]
            xmls += [os.path.join(dirpath, fn) for fn in filenames if fn.endswith('.xml')]
        for fp in sorted(xmls):
            try:
                root = ET.parse(fp).getroot()
            except ET.ParseError:
                continue
            for mi in root.iter('menuitem'):
                act = mi.get('action')
                if act:
                    menu_targets.add(_full_id(module, act))
            for rec in root.iter('record'):
                mdl = rec.get('model')
                rid = rec.get('id') or ''
                fid = _full_id(module, rid) if rid else f'__anon__.{module}.{len(win)+len(rep)}'
                fs = _fields_of_record(rec)
                if mdl == 'ir.actions.act_window':
                    new = {
                        'module': module,
                        'name': (fs['name'].text or '').strip() if 'name' in fs else '',
                        'res_model': (fs['res_model'].text or '').strip() if 'res_model' in fs else '',
                        'view_mode': (fs['view_mode'].text or '').strip() if 'view_mode' in fs else '',
                        'view_id': fs['view_id'].get('ref') if 'view_id' in fs else '',
                        'binding_model': fs['binding_model_id'].get('ref') if 'binding_model_id' in fs else '',
                        'domain': (fs['domain'].text or '').strip() if 'domain' in fs and fs['domain'].text else '',
                        'context': ((fs['context'].get('eval') or fs['context'].text or '').strip()
                                    if 'context' in fs else ''),
                        'view_bindings': _inline_view_bindings(fs['view_ids'].get('eval') or ''
                                                               if 'view_ids' in fs else '', module),
                    }
                    if fid in win:
                        win[fid].update({k: v for k, v in new.items()
                                         if v and k != 'module'})
                    else:
                        win[fid] = new
                elif mdl == 'ir.actions.act_window.view':
                    _aw = fs['act_window_id'].get('ref') if 'act_window_id' in fs else None
                    _vm = (fs['view_mode'].text or '').strip() if 'view_mode' in fs and fs['view_mode'].text else ''
                    _vi = fs['view_id'].get('ref') if 'view_id' in fs else None
                    if _aw and _vm and _vi:
                        AW_VIEW_BINDINGS.setdefault(_full_id(module, _aw), {})[_vm] = _full_id(module, _vi)
                elif mdl == 'ir.actions.report':
                    new = {
                        'module': module,
                        'name': (fs['name'].text or '').strip() if 'name' in fs else '',
                        'model': (fs['model'].text or '').strip() if 'model' in fs else '',
                        'report_type': (fs['report_type'].text or '').strip() if 'report_type' in fs else '',
                        'report_name': (fs['report_name'].text or '').strip() if 'report_name' in fs else '',
                        'binding_model': fs['binding_model_id'].get('ref') if 'binding_model_id' in fs else '',
                    }
                    if fid in rep:
                        rep[fid].update({k: v for k, v in new.items()
                                         if v and k != 'module'})
                    else:
                        rep[fid] = new
                elif mdl == 'ir.actions.server':
                    ref = fs['model_id'].get('ref') if 'model_id' in fs else ''
                    srv[fid] = {
                        'module': module,
                        'name': (fs['name'].text or '').strip() if 'name' in fs else '',
                        'model_ref': ref or '',
                        'state': (fs['state'].text or '').strip() if 'state' in fs else '',
                        'binding_model': fs['binding_model_id'].get('ref') if 'binding_model_id' in fs else '',
                    }
                elif mdl == 'website.menu':
                    wmenu[fid] = {'module': module,
                                  'url': (fs['url'].text or '').strip() if 'url' in fs else ''}
                elif mdl == 'ir.cron':
                    cron[fid] = {
                        'module': module,
                        'name': (fs['name'].text or '').strip() if 'name' in fs else '',
                        'model_ref': fs['model_id'].get('ref') if 'model_id' in fs else '',
                        'has_code': 'code' in fs,
                    }
                elif mdl == 'ir.ui.menu':
                    if 'action' in fs:
                        ref = fs['action'].get('ref')
                        if ref:
                            menu_targets.add(_full_id(module, ref))
            for tpl in root.iter('template'):
                tid = tpl.get('id')
                if not tid:
                    continue
                if (tpl.get('active') or '').strip() in ('False', 'false', '0'):
                    continue
                inh = tpl.get('inherit_id')
                tpl_recs[_full_id(module, tid)] = {
                    'module': module, 'elem': copy.deepcopy(tpl),
                    'inherit': _full_id(module, inh) if inh else None,
                    'primary': (tpl.get('primary') or '').strip() in ('True', 'true', '1'),
                    'prio': int(tpl.get('priority') or 16), 'idx': len(tpl_recs)}
    templates.update(_combine_templates(tpl_recs))
    return {'window': win, 'report': rep, 'server': srv, 'cron': cron,
            'webmenu': wmenu, 'menu_targets': menu_targets, 'templates': templates}


BTN_XID = re.compile(r'^%\((.+?)\)d$')

def collect_buttons(merged, registry):
    out = []
    for vid, v in merged.items():
        model = v['model']
        if not model:
            continue
        def walk(elem, cur_model):
            for child in elem:
                if child.tag == 'button' and child.get('name'):
                    bt = child.get('type', 'object')
                    nm = child.get('name')
                    tgt = ''
                    if bt == 'action':
                        m = BTN_XID.match(nm)
                        tgt = m.group(1) if m else nm
                    out.append({'model': cur_model, 'view': vid, 'name': nm,
                                'btype': bt, 'target': tgt})
                if child.tag == 'field' and child.get('name'):
                    sub = [c for c in child if c.tag in VIEW_TAGS]
                    if sub:
                        ent = registry.get(cur_model)
                        rel = None
                        if ent and child.get('name') in ent['fields']:
                            rel = ent['fields'][child.get('name')].get('relation')
                        for sv in sub:
                            walk(sv, rel or cur_model)
                else:
                    walk(child, cur_model)
        arch = v['arch']
        direct = [c for c in arch if c.tag in VIEW_TAGS]
        for t in direct:
            walk(t, model)
    return out


def view_stats(merged, registry):
    stats = []
    for vid, v in merged.items():
        model, arch = v['model'], v['arch']
        if not model:
            continue
        direct = [c for c in arch if c.tag in VIEW_TAGS]
        for t in direct:
            seen_pairs = []
            vis_c, vis_e = set(), set()
            btn_names = []
            n_btn = 0
            stack = [(t, model, False, False)]
            while stack:
                el, cur_model, hc, he = stack.pop()
                ent = registry.get(cur_model) or {}
                sub_model = None
                if el.tag == 'field' and el.get('name') and len(el):
                    if any(ch.tag in ('list', 'tree', 'form', 'kanban') for ch in el):
                        fdef = ent.get('fields', {}).get(el.get('name'), {})
                        sub_model = fdef.get('relation') or None
                        for sub in el.iter('field'):
                            if sub is el or not sub.get('name'):
                                continue
                            if sub.get('invisible') in LITERAL_TRUE or \
                               sub.get('column_invisible') in LITERAL_TRUE:
                                continue
                            if (sub.get('widget') or '').strip() in STRUCTURAL_WIDGETS:
                                continue
                            EMBEDDED_FIELDS.setdefault(
                                (cur_model, el.get('name')), set()).add(sub.get('name'))
                for child in el:
                    child_model = sub_model if (sub_model and child.tag in
                                               ('list', 'tree', 'form', 'kanban')) else cur_model
                    _h = _id_hidden(child.get('invisible'))
                    c_hc, c_he = hc or _h[0], he or _h[1]
                    if child.tag == 'field' and child.get('name'):
                        inv = child.get('invisible')
                        cinv = child.get('column_invisible')
                        wid = (child.get('widget') or '').strip()
                        fname = child.get('name')
                        if inv in LITERAL_TRUE or cinv in LITERAL_TRUE:
                            pass
                        elif wid in STRUCTURAL_WIDGETS:
                            WIDGET_SEEN.setdefault((cur_model, fname), set()).add(wid)
                            pass
                        else:
                            WIDGET_SEEN.setdefault((cur_model, fname), set()).add(wid or '')
                            if (cur_model, fname) not in seen_pairs:
                                seen_pairs.append((cur_model, fname))
                            if not c_hc:
                                vis_c.add((cur_model, fname))
                            if not c_he:
                                vis_e.add((cur_model, fname))
                    elif child.tag == 'button':
                        n_btn += 1
                        bn = (child.get('name') or '').strip()
                        if bn and bn not in btn_names:
                            btn_names.append(bn)
                    elif child.tag == 'control':
                        creates = [c for c in child if c.tag == 'create']
                        if len(creates) > 1:
                            for c in creates:
                                for fname in re.findall(r"default_(\w+)", c.get('context') or ''):
                                    if (cur_model, fname) not in seen_pairs:
                                        seen_pairs.append((cur_model, fname))
                                    if not c_hc:
                                        vis_c.add((cur_model, fname))
                                    if not c_he:
                                        vis_e.add((cur_model, fname))
                    stack.append((child, child_model, c_hc, c_he))
            g = {}
            for attr, key in (('create', 'gate_create'), ('edit', 'gate_write'),
                              ('delete', 'gate_unlink')):
                raw = (t.get(attr) or '').strip()
                g[key] = False if raw in ('0', 'False', 'false') else True
            dims = []
            if t.tag in ('pivot', 'graph'):
                for f in t.iter('field'):
                    if f.get('type') in ('row', 'col') and f.get('name'):
                        if f.get('name') not in dims:
                            dims.append(f.get('name'))
            det_names = []
            for _m, _f in seen_pairs:
                if _f not in det_names:
                    det_names.append(_f)
            stats.append({'view': vid, 'model': model, 'vtype': t.tag,
                          'n_visible_fields': len(seen_pairs), 'n_buttons': n_btn,
                          'det_fields': det_names,
                          'det_pairs': list(seen_pairs),
                          'det_not_create': [p for p in seen_pairs if p not in vis_c],
                          'det_not_edit': [p for p in seen_pairs if p not in vis_e],
                          'btn_names': list(btn_names),
                          'dim_fields': dims,
                          'priority': v.get('priority', 16),
                          **g})
    return stats


FOLDED_MAP = {}
SCOPE_MODULES = None
EMBEDDED_FIELDS = {}
WIDGET_SEEN = {}
STRUCTURAL_WIDGETS = set()
NUMERIC_TTYPES = {'integer', 'float', 'monetary'}
GROUPABLE_TTYPES = {'selection', 'many2one', 'date', 'datetime', 'boolean'}

def pivot_pool_arch(merged):
    out = {}
    for v in merged.values():
        model, arch = v['model'], v['arch']
        if not model:
            continue
        for t in [c for c in arch.iter() if c.tag in ('pivot', 'graph')]:
            meas, dims = out.setdefault(model, (set(), set()))
            for f in t.iter('field'):
                ty, name = f.get('type'), f.get('name')
                if not name:
                    continue
                if ty == 'measure':
                    meas.add(name)
                elif ty in ('row', 'col'):
                    dims.add(name)
    return out

def eo_det_pools(model, arch_pools, registry):
    meas, dims = (set(s) for s in arch_pools.get(model, (set(), set())))
    return meas, dims


def eo_det_for(model, arch_pools, registry):
    meas, dims = eo_det_pools(model, arch_pools, registry)
    return len(meas) + len(dims) + 1 if registry.get(model) else 0


def parse_access(order, modules, registry):
    san = {m.replace('.', '_'): m for m in registry}
    acl = {}
    for module in order:
        p = os.path.join(modules[module], 'security', 'ir.model.access.csv')
        if not os.path.isfile(p):
            continue
        try:
            rows = list(csv.DictReader(open(p, encoding='utf-8')))
        except Exception as e:
            WARN.add('ACL', f'{p}: {e}')
            continue
        for r in rows:
            ref = (r.get('model_id:id') or r.get('model_id/id') or '').strip()
            body = ref.split('.', 1)[-1]
            tech = san.get(body[len('model_'):]) if body.startswith('model_') else None
            if not tech:
                continue
            a = acl.setdefault(tech, {'create': False, 'write': False, 'unlink': False})
            for k, col in (('create', 'perm_create'), ('write', 'perm_write'),
                           ('unlink', 'perm_unlink')):
                if str(r.get(col, '')).strip() in ('1', 'True', 'true'):
                    a[k] = True
    return acl


def compute_switches(merged):
    sw = {}
    for vid, v in merged.items():
        model, arch = v['model'], v['arch']
        if not model:
            continue
        for t in [c for c in arch if c.tag in VIEW_TAGS]:
            key = (model, 'list' if t.tag == 'tree' else t.tag)
            cur = sw.setdefault(key, {'create': False, 'edit': False, 'delete': False,
                                      'editable': False})
            for attr in ('create', 'edit', 'delete'):
                if t.get(attr) != 'false':
                    cur[attr] = True
            if t.tag in ('list', 'tree') and t.get('editable'):
                cur['editable'] = True
    return sw


def _related_hosts(model, path, registry):
    hosts, cur = [], model
    toks = [t for t in path.split('.') if t]
    for i, tok in enumerate(toks):
        ent = registry.get(cur)
        if not ent or tok not in ent['fields']:
            break
        hosts.append(cur)
        fld = ent['fields'][tok]
        rel = fld.get('relation')
        if rel:
            if i == len(toks) - 1:
                break
            cur = rel
        else:
            break
    return hosts


def _ftr_signals_one(model, registry):
    out = set()
    ent = registry.get(model)
    if not ent:
        return out
    for fe in ent['fields'].values():
        if fe.get('via') == 'delegated':
            continue
        if not fe.get('is_user_visible'):
            continue
        if fe.get('is_auto'):
            continue
        rel = fe.get('relation')
        if rel and not fe.get('is_related'):
            out.add(rel)
        rp = fe.get('related')
        if rp and rp != '<dynamic>':
            out.update(_related_hosts(model, rp, registry))
    return out


def ftr_signals_for_fields(model, fnames, registry, folded=None):
    fold_map = folded or {}
    models = [model] + [ch for ch, p in fold_map.items() if p == model]
    want = set(fnames or [])
    out = set()
    for m in models:
        e = registry.get(m)
        if not e:
            continue
        for fn, fe in e['fields'].items():
            if fn not in want or fe.get('is_auto'):
                continue
            rel = fe.get('relation')
            if rel and not fe.get('is_related'):
                out.add(rel)
            rp = fe.get('related')
            if rp and rp != '<dynamic>':
                out.update(_related_hosts(m, rp, registry))
    def ok(t):
        e = registry.get(t)
        return (e and model_kind(e) == 'persistent'
                and t != model and t not in fold_map)
    return {t for t in out if ok(t)}


def ftr_signals(model, registry, folded=None):
    out = _ftr_signals_one(model, registry)
    children = [c for c, p in (folded or {}).items() if p == model]
    for ch in children:
        out |= _ftr_signals_one(ch, registry)
    fold_map = folded or {}
    def ok(m):
        e = registry.get(m)
        return (e and model_kind(e) == 'persistent'
                and m != model
                and m not in fold_map)
    return {m for m in out if ok(m)}


def filter_fields_by_scope(registry, scope_modules):
    dropped = 0
    for model, e in registry.items():
        keep = {}
        for fn, fe in e['fields'].items():
            src = fe.get('contributed_by')
            if src is None or src in scope_modules:
                keep[fn] = fe
            else:
                dropped += 1
        e['fields'] = keep
    if dropped:
        WARN.add('SCOPEFILTER', f'{dropped} fields from modules outside the scope removed')
    return dropped


def enrich_window_actions(acts, buttons, vstats, switches, acl, registry, merged=None):
    btn_targets = {b['target'] for b in buttons if b['btype'] == 'action' and b['target']}
    stats_by = {}
    for s in vstats:
        stats_by.setdefault((s['model'], s['vtype']), []).append(s)
    arch_pools = pivot_pool_arch(merged) if merged is not None else {}
    for fid, a in acts['window'].items():
        M = a['res_model']
        a['reach_kind'] = ('menu' if fid in acts['menu_targets'] else
                           'button_action' if fid in btn_targets else
                           'binding' if a.get('binding_model') else 'none')
        _modes = [('list' if x.strip() == 'tree' else x.strip())
                  for x in (a.get('view_mode') or 'list,form').split(',') if x.strip()]
        _bind = dict(a.get('view_bindings') or {})
        _bind.update(AW_VIEW_BINDINGS.get(fid, {}))
        _modes += [('list' if k == 'tree' else k) for k in _bind if ('list' if k == 'tree' else k) not in _modes]
        _vid = (a.get('view_id') or '').strip()
        def _pick(vtype):
            if vtype not in _modes:
                return None
            pool = stats_by.get((M, vtype), []) + (stats_by.get((M, 'tree'), []) if vtype == 'list' else [])
            if not pool:
                return None
            def _match(ref):
                if not ref:
                    return None
                for s in pool:
                    if s['view'] == ref or ('.' not in ref and s['view'].endswith('.' + ref)):
                        return s
                return None
            for ref in (_bind.get(vtype) or _bind.get('tree' if vtype == 'list' else ''),
                        _vid, _context_view_ref(a.get('context'), vtype, a.get('module', ''))):
                s = _match(ref)
                if s is not None:
                    return s
            return min(pool, key=lambda s: (int(s.get('priority', 16)), s['view']))
        bestl = _pick('list')
        if bestl is not None:
            a['eq_det_raw'] = int(bestl['n_visible_fields']) + 1
            a['eq_det_fields'] = list(bestl.get('det_fields', []))
            a.setdefault('uses_views', []).append(bestl['view'])
        else:
            a['eq_det_raw'] = 0
            a['eq_det_fields'] = []
        best = _pick('form')
        if best is not None:
            a.setdefault('uses_views', []).append(best['view'])
            a['det_raw'] = int(best['n_visible_fields']) + int(best['n_buttons']) + 1
            a['det_fields'] = list(best.get('det_fields', []))
        else:
            a['det_raw'] = 0
            a['det_fields'] = []
        ent = registry.get(M)
        base = {M} if ent and model_kind(ent) == 'persistent' else set()
        _sig = ftr_signals(M, registry, FOLDED_MAP)
        if SCOPE_MODULES:
            _sig = {t for t in _sig
                    if (registry.get(t) or {}).get('host') in SCOPE_MODULES}
        a['ftr'] = sorted(base | _sig)
        _lsig = ftr_signals_for_fields(M, a.get('eq_det_fields', []),
                                       registry, FOLDED_MAP)
        if SCOPE_MODULES:
            _lsig = {t for t in _lsig
                     if (registry.get(t) or {}).get('host') in SCOPE_MODULES}
        a['ftr_list'] = sorted(base | _lsig)
        toks = set((a['view_mode'] or 'list,form').split(','))
        for _vt in ('pivot', 'graph'):
            _s = _pick(_vt)
            if _s is not None:
                a.setdefault('uses_views', []).append(_s['view'])
        if toks & {'pivot', 'graph', 'cohort'}:
            a['eo_det_raw'] = eo_det_for(M, arch_pools, registry)
            _meas, _dims = eo_det_pools(M, arch_pools, registry)
            a['eo_measures'] = sorted(_meas)
            a['eo_dimensions'] = sorted(_dims)
            _ent = registry.get(M) or {'fields': {}}
            _ftr = set()
            for _dn in _dims:
                _fe = _ent['fields'].get(_dn)
                if not _fe or _fe.get('is_auto'):
                    continue
                _rel = _fe.get('relation')
                if _rel and (registry.get(_rel) or {}).get('kind') != 'abstract':
                    _ftr.add(_rel)
            if SCOPE_MODULES:
                _ftr = {t for t in _ftr
                        if (registry.get(t) or {}).get('host') in SCOPE_MODULES}
            a['eo_ftr'] = sorted(_ftr)
        else:
            a['eo_det_raw'] = 0
            a['eo_measures'] = a['eo_dimensions'] = a['eo_ftr'] = []
        sw = switches.get((M, 'form'), {'create': True, 'edit': True, 'delete': True})
        ac = acl.get(M, {'create': False, 'write': False, 'unlink': False})
        a['gate_create'] = sw.get('create', True) and ac['create']
        a['gate_write'] = sw.get('edit', True) and ac['write']
        a['gate_unlink'] = sw.get('delete', True) and ac['unlink']



# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description='Static extractor of Odoo metadata into the ABox')
    ap.add_argument('--addons', nargs='+', required=True, help='addons root directories (one or more)')
    ap.add_argument('--scope', nargs='*', default=[], help='module names of the counting scope')
    ap.add_argument('--scope-file', help='file listing the scope modules, one per line')
    ap.add_argument('--restrict', action='store_true',
                    help='load only the scope modules (views, menus and actions of other modules are ignored)')
    ap.add_argument('--out', default='./extract_out')
    ap.add_argument('--ttl', action='store_true', help='write abox.ttl')
    ap.add_argument('--closure', action='store_true',
                    help='expand --scope to its manifest dependency closure')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    modules = discover_modules(args.addons)
    print(f'modules found: {len(modules)}')
    if args.scope_file:
        with open(args.scope_file, encoding='utf-8') as _sf:
            args.scope = list(dict.fromkeys(args.scope + [l.split()[0] for l in _sf
                                                          if l.strip() and not l.startswith('#')]))
    if args.restrict:
        _missing = [s for s in args.scope if s not in modules]
        if _missing:
            print(f'!! scope modules not found: {_missing}', file=sys.stderr)
        modules = {m: p for m, p in modules.items() if m in set(args.scope)}
        print(f'scope modules loaded: {len(modules)}')
    for s in args.scope:
        if s not in modules:
            print(f'!! scope module {s} not found', file=sys.stderr)

    depends = {}
    for m, p in modules.items():
        depends[m] = [d for d in parse_manifest(os.path.join(p, '__manifest__.py'))
                      .get('depends', []) if isinstance(d, str)]
    order = topo_order(modules, depends)

    module_decls = {m: collect_module_declarations(m, modules[m]) for m in order}
    n_cls = sum(len(v) for v in module_decls.values())
    print(f'model class declarations collected: {n_cls}')

    registry = resolve_registry(order, module_decls)          # P1+P2
    inject_auto_fields(registry)                              # P3
    inject_delegated_fields(registry)
    finalize_registry(registry)                               # P4
    merged = merge_views(order, modules)
    print(f'views merged: {len(merged)} root views')
    scan_visibility(merged, registry)
    propagate_delegated_visibility(registry)
    acts = collect_actions(order, modules)
    _nm = mark_report_output_fields(acts, registry)
    print(f'report output fields marked visible: {_nm}')
    propagate_delegated_visibility(registry)
    buttons = collect_buttons(merged, registry)
    vstats = view_stats(merged, registry)
    switches = compute_switches(merged)
    acl = parse_access(order, modules, registry)
    scope = set(args.scope)
    import ast as _ast
    for m, p in modules.items():
        try:
            d = _ast.literal_eval(open(os.path.join(p, '__manifest__.py'),
                                       encoding='utf-8').read())
            MANIFEST_INFO[m] = {'application': bool(d.get('application')),
                                'depends': list(d.get('depends') or [])}
        except Exception:
            MANIFEST_INFO[m] = {'application': False, 'depends': []}
    if args.closure:
        man = {m: v['depends'] for m, v in MANIFEST_INFO.items()}
        stk = list(scope)
        while stk:
            for d in man.get(stk.pop(), []):
                if d in modules and d not in scope:
                    scope.add(d); stk.append(d)
        print(f'scope expanded: {len(args.scope)} -> {len(scope)} modules')
    if args.restrict:
        scope = set(modules)
    globals()['SCOPE_MODULES'] = set(scope)
    n_dropped = filter_fields_by_scope(registry, scope)
    print(f'fields from out-of-scope modules removed: {n_dropped}')
    folded, eif_exposed = compute_folding_and_eif(registry, acts, scope)
    globals()['FOLDED_MAP'] = dict(folded)
    enrich_window_actions(acts, buttons, vstats, switches, acl, registry, merged)
    print(f'transactions: window {len(acts["window"])} / report {len(acts["report"])} / '
          f'server {len(acts["server"])} / menu targets {len(acts["menu_targets"])} / '
          f'buttons {len(buttons)} / templates {len(acts["templates"])}')



    outs = []
    if args.ttl:
        outs.append(emit_ttl(registry, scope, args.out, acl))
        emit_ttl_txn(acts, buttons, registry, scope, args.out, vstats)
        with open(os.path.join(args.out, 'abox.ttl'), 'a', encoding='utf-8') as f:
            f.write('\n# Subgroups and fields exposed to EIFs\n')
            for child, parent in sorted(folded.items()):
                f.write(f':model_{sanitize(child)} :foldedInto :model_{sanitize(parent)} .\n')
            for src, fs in sorted(eif_exposed.items()):
                for fn in sorted(fs):
                    _fname, _owner = fn if isinstance(fn, tuple) else (fn, src)
                    _fu = _fld_uri(_owner, _fname, registry, folded)
                    if _fu:
                        f.write(f':model_{sanitize(src)} :eifField {_fu} .\n')
            for (src, relname), subnames in sorted(EMBEDDED_FIELDS.items()):
                sent = registry.get(src)
                if not sent:
                    continue
                fe = sent['fields'].get(relname)
                tgt = fe.get('relation') if fe else None
                tent = registry.get(tgt) if tgt else None
                if not tent or model_kind(tent) != 'persistent':
                    continue
                if tent['host'] == sent['host']:
                    continue
                if SCOPE_MODULES and tent['host'] not in SCOPE_MODULES:
                    continue
                for sn in sorted(subnames):
                    _u = _fld_uri(tgt, sn, registry, folded)
                    if _u:
                        f.write(f':model_{sanitize(src)} :eifField {_u} .\n')

    print(f'subgroups: {len(folded)} | fields exposed to EIFs: '
          f'{sum(len(v) for v in eif_exposed.values())} ({len(eif_exposed)} tables)')
    wpath = os.path.join(args.out, 'warnings.log')
    WARN.dump(wpath)
    outs.append(wpath)

    n_models = len(registry)
    n_fields = sum(len(e['fields']) for e in registry.values())
    in_scope_models = [m for m, e in registry.items() if e['host'] in scope]
    print(f'registry: {n_models} models / {n_fields} fields; '
          f'scope models {len(in_scope_models)}; warnings {len(WARN.items)} (see warnings.log)')
    for o in outs:
        print('output:', o)



def _fld_uri(model, fname, registry, folded=None):
    ent = registry.get(model)
    if ent and fname in ent.get('fields', {}):
        return f':field_{sanitize(model)}__{sanitize(fname)}'
    for ch, pa in (folded or FOLDED_MAP).items():
        if pa == model:
            che = registry.get(ch)
            if che and fname in che.get('fields', {}):
                return f':field_{sanitize(ch)}__{sanitize(fname)}'
    return None



def emit_views_ttl(f, vstats, merged, registry, folded):
    seen = set()
    for s in vstats:
        vid, model = s['view'], s['model']
        if vid in seen or model not in registry:
            continue
        seen.add(vid)
        n = f':view_{sanitize(vid)}'
        f.write(f'\n{n} a :OdooView ;\n')
        f.write(f'    :ofModel :model_{sanitize(model)} ;\n')
        f.write(f'    :viewType "{s["vtype"]}" ;\n')
        f.write(f'    :gateCreate {ttl_bool(s.get("gate_create", True))} ; '
                f':gateWrite {ttl_bool(s.get("gate_write", True))} ; '
                f':gateUnlink {ttl_bool(s.get("gate_unlink", True))} ')
        for fmodel, fn in s.get('det_pairs', [(model, x) for x in s.get('det_fields', [])]):
            u = _fld_uri(fmodel, fn, registry, folded)
            if u:
                f.write(f';\n    :viewDetField {u} ')
        for key, prop in (('det_not_create', 'viewDetFieldNotOnCreate'), ('det_not_edit', 'viewDetFieldNotOnEdit')):
            for fmodel, fn in s.get(key, []):
                u = _fld_uri(fmodel, fn, registry, folded)
                if u:
                    f.write(f';\n    :{prop} {u} ')
        def _tgts(names):
            out = {model}
            for fn in names:
                ent = registry.get(model) or {'fields': {}}
                fe = ent['fields'].get(fn)
                if not fe:
                    for ch, pa in (folded or {}).items():
                        if pa == model and fn in (registry.get(ch) or {'fields': {}})['fields']:
                            fe = registry[ch]['fields'][fn]
                            break
                cand = []
                if fe:
                    cand.append(fe.get('relation'))
                    cand.append(fe.get('delegated_from'))
                for rel in cand:
                    if rel and rel in registry and model_kind(registry[rel]) == 'persistent':
                        if not (SCOPE_MODULES and registry[rel].get('host') not in SCOPE_MODULES):
                            out.add(rel)
            return out
        tgts = _tgts(s.get('det_fields', []))
        _nc = {fn for _m, fn in s.get('det_not_create', [])}
        _ne = {fn for _m, fn in s.get('det_not_edit', [])}
        _all = s.get('det_fields', [])
        for _t in sorted(tgts - _tgts([x for x in _all if x not in _nc])):
            f.write(f';\n    :viewFtrRefNotOnCreate :model_{sanitize(_t)} ')
        for _t in sorted(tgts - _tgts([x for x in _all if x not in _ne])):
            f.write(f';\n    :viewFtrRefNotOnEdit :model_{sanitize(_t)} ')
        for t in sorted(tgts):
            f.write(f';\n    :viewFtrRef :model_{sanitize(t)} ')
        for bn in s.get('btn_names', []):
            f.write(f';\n    :hasButton :btn_{sanitize(model)}__{sanitize(bn)} ')
        f.write('.\n')
    return len(seen)





def template_root_models(acts):
    tpls = acts.get('templates', {})
    def _find(tid):
        if tid in tpls:
            return tid
        for k in tpls:
            if k.split('.', 1)[-1] == tid.split('.', 1)[-1]:
                return k
        return None
    root = {}
    queue = []
    for _ra in acts.get('report', {}).values():
        _rn, _mdl = _ra.get('report_name'), _ra.get('model')
        k = _find(_rn) if _rn else None
        if k and _mdl and k not in root:
            root[k] = _mdl
            queue.append(k)
    while queue:
        k = queue.pop()
        for c in tpls[k].get('tcalls', []):
            ck = _find(c)
            if ck and ck not in root:
                root[ck] = root[k]
                queue.append(ck)
    return root, _find




def _expand_inherited(chain, inh):
    seen = set()
    while chain:
        head = chain.split('.', 1)[0]
        if head not in inh or head in seen:
            return chain
        seen.add(head)
        chain = _subst(chain, inh)
    return None


def _resolve_platform(chain, report_model, registry):
    head, _, rest = chain.partition('.')
    if head not in PLATFORM_VARS:
        return None
    cur = report_model if head == 'docs' else PLATFORM_VARS[head]
    models, fields = {cur}, []
    for seg in [s for s in rest.split('.') if s]:
        ent = registry.get(cur)
        if not ent or seg not in ent.get('fields', {}):
            break
        fields.append((cur, seg))
        nxt = ent['fields'][seg].get('relation')
        if not nxt:
            break
        models.add(nxt)
        cur = nxt
    return models, fields


def analyze_reports(acts, registry, folded=None):
    tpls = acts.get('templates', {})
    _root, find = template_root_models(acts)
    res = {}
    for ra in acts.get('report', {}).values():
        rn, mdl = ra.get('report_name'), ra.get('model')
        k = find(rn) if rn else None
        if not k or not mdl or k in res:
            continue
        keys, refs, landing, visible, n_all = set(), {mdl}, set(), set(), 0
        seen, stack = set(), [(k, {})]
        while stack:
            cur, inh = stack.pop()
            sig = (cur, tuple(sorted((a, b or '') for a, b in inh.items())))
            if sig in seen or cur not in tpls:
                continue
            seen.add(sig)
            for expr, chains in tpls[cur].get('outputs', []):
                n_all += 1
                hit = set()
                for ch in chains:
                    full = _expand_inherited(ch, inh)
                    r = _resolve_platform(full, mdl, registry) if full else None
                    if r and r[1]:
                        refs |= r[0]
                        visible |= set(r[1])
                        hit.add(r[1][-1])
                if hit:
                    keys |= {('f',) + h for h in hit}
                    landing |= hit
                else:
                    keys.add(('x', expr))
            for callee, cenv in tpls[cur].get('calls', []):
                ck = find(callee)
                if ck:
                    new = dict(inh)
                    for var, val in cenv.items():
                        new[var] = _expand_inherited(val, inh) if val is not None else None
                    stack.append((ck, new))
        res[k] = {'model': mdl, 'distinct': len(keys), 'n_all': n_all, 'refs': refs,
                  'landing': landing, 'visible': visible}
    return res


def mark_report_output_fields(acts, registry):
    n_marked = 0
    for r in analyze_reports(acts, registry).values():
        for model, fname in r['visible']:
            fe = registry[model]['fields'][fname]
            if not fe.get('is_user_visible'):
                fe['is_user_visible'] = True
                n_marked += 1
            fe['view_types'] = ','.join(sorted((set((fe.get('view_types') or '').split(',')) | {'report'}) - {''}))
    return n_marked


def emit_ttl_txn(acts, buttons, registry, scope, outdir, vstats=None):
    path = os.path.join(outdir, 'abox.ttl')
    tpl_root_model, _tpl_find = template_root_models(acts)
    for _k in list(tpl_root_model):
        tpl_root_model[_k.split('.', 1)[-1]] = tpl_root_model[_k]
    _rep = analyze_reports(acts, registry, FOLDED_MAP)

    with open(path, 'a', encoding='utf-8') as f:
        f.write('\n# ===== Views =====\n')
        if vstats:
            n_v = emit_views_ttl(f, vstats, None, registry, FOLDED_MAP)
            print(f'views: {n_v}')
        f.write('\n# ===== Transactions =====\n')
        for fid, a in sorted(acts['window'].items()):
            if fid.startswith('__anon__') or not a['res_model']:
                continue
            aid = ':wact_' + sanitize(fid)
            f.write(f'\n{aid} a :WindowAction ;\n')
            f.write(f'    :actsOn :model_{sanitize(a["res_model"])} ;\n')
            for tok in (a['view_mode'] or 'list,form').split(','):
                if tok.strip():
                    f.write(f'    :viewModeToken "{tok.strip()}" ;\n')
            f.write(f'    :reachKind "{a.get("reach_kind", "none")}" ;\n')
            for _vw in dict.fromkeys(a.get('uses_views', [])):
                f.write(f'    :usesView :view_{sanitize(_vw)} ;\n')
            f.write(f'    :declaredIn :module_{sanitize(a.get("module", "?"))} .\n')
        for fid, a in sorted(acts['report'].items()):
            if fid.startswith('__anon__') or not a['model']:
                continue
            f.write(f'\n:ract_{sanitize(fid)} a :ReportAction ;\n')
            if a.get('report_name'):
                if a['report_name'] in acts.get('templates', {}):
                    f.write(f'    :usesTemplate :tpl_{sanitize(a["report_name"])} ;\n')
            f.write(f'    :actsOn :model_{sanitize(a["model"])} ;\n')
            f.write(f'    :declaredIn :module_{sanitize(a.get("module", "?"))} .\n')
        san = {m.replace('.', '_'): m for m in registry}
        for fid, a in sorted(acts['server'].items()):
            ref = (a['model_ref'] or '').split('.', 1)[-1]
            tech = san.get(ref[len('model_'):], '') if ref.startswith('model_') else ''
            if fid.startswith('__anon__') or not tech:
                continue
            f.write(f'\n:sact_{sanitize(fid)} a :ServerAction ;\n')
            f.write(f'    :xmlid "{fid}" ; :srvState "{a["state"]}" ;\n')
            f.write(f'    :actsOn :model_{sanitize(tech)} ;\n')
            f.write(f'    :declaredIn :module_{sanitize(a.get("module", "?"))} .\n')
        for tid, t in sorted(acts.get('templates', {}).items()):
            f.write(f'\n:tpl_{sanitize(tid)} a :QWebTemplate ;\n')
            f.write(f'    :nTField {int(t.get("n_tfield", 0))} ;\n')
            f.write(f'    :nTEsc {int(t.get("n_tesc", 0))} ;\n')
            if tid in _rep:
                _r = _rep[tid]
                f.write(f'    :nOutputDistinct {int(_r["distinct"])} ;\n')
                for _u in sorted({_fld_uri(m, fn, registry, FOLDED_MAP) for m, fn in _r['landing']} - {None}):
                    f.write(f'    :templateDetField {_u} ;\n')
                for _m in sorted(_r['refs']):
                    if _m in registry:
                        f.write(f'    :templateFtrRef :model_{sanitize(_m)} ;\n')
            for _c in sorted(set(t.get('tcalls') or [])):
                _ck = _tpl_find(_c)
                if _ck:
                    f.write(f'    :tCalls :tpl_{sanitize(_ck)} ;\n')
            f.write('    a :QWebTemplate .\n')
        seen = set()
        for b in buttons:
            if b['btype'] != 'object':
                continue
            key = (b['model'], b['name'])
            if key in seen or not b['model']:
                continue
            seen.add(key)
            f.write(f'\n:btn_{sanitize(b["model"])}__{sanitize(b["name"])} a :ObjectButton ;\n')
            f.write(f'    :onModel :model_{sanitize(b["model"])} ;\n')
            f.write(f'    :buttonName "{b["name"]}" .\n')
    return path



if __name__ == '__main__':
    main()
