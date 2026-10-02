#!/usr/bin/env python3
"""build_scope.py - counting scope: the 34 applications and their manifest dependency closure.

Auto-installed extension modules are not included; the reference installation
used for manual counting must contain exactly these modules.

Usage:
  python scripts/build_scope.py --addons ODOO_ROOT/addons ODOO_ROOT/odoo/addons --out data/scope.txt
"""
import argparse, ast, os
APPS = ("crm point_of_sale pos_restaurant sale_management contacts mass_mailing survey marketing_card "
        "website_event mass_mailing_sms stock mrp purchase maintenance repair hr fleet hr_holidays "
        "hr_recruitment hr_attendance hr_expense hr_skills lunch account website website_sale "
        "website_slides im_livechat website_hr_recruitment project mail data_recycle calendar project_todo").split()
ap = argparse.ArgumentParser()
ap.add_argument('--addons', nargs='+', required=True)
ap.add_argument('--out', default='scope.txt')
a = ap.parse_args()
man = {}
for base in a.addons:
    for m in os.listdir(base):
        f = os.path.join(base, m, '__manifest__.py')
        if os.path.isfile(f):
            try:
                man[m] = ast.literal_eval(open(f, encoding='utf-8').read())
            except Exception:
                pass
missing = [m for m in APPS if m not in man]
if missing:
    raise SystemExit(f'applications not found: {missing}')
scope, stack = set(APPS), list(APPS)
while stack:
    for d in man.get(stack.pop(), {}).get('depends', []):
        if d in man and d not in scope:
            scope.add(d); stack.append(d)
with open(a.out, 'w', encoding='utf-8') as fh:
    fh.write(f'# Counting scope: 34 applications and their dependency closure, {len(scope)} modules (no auto-installed extensions)\n')
    for m in sorted(scope):
        fh.write(f"{m}\t# {'app' if m in APPS else 'dep'}\n")
print(f'applications {len(APPS)}; with dependencies {len(scope)} -> {a.out}')
