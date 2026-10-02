# Odoo IFPUG Counting from Declarative Metadata

Ontology-based IFPUG function point counting of **Odoo 19.0 Community**.
The functional size of each application is counted from the platform's declarative
metadata (models, fields, views, actions, menus, report templates) with SPARQL rules.
Entry points whose behaviour lies in Python method bodies (object buttons, HTTP routes,
server actions with code) are estimated with parameters calibrated on a development set
from AI verdicts.

- Counting scope: the 34 Odoo applications and their dependency closure (82 modules).
- Ontology namespace: `http://example.org/odoo-ifpug#`
- Triple store: GraphDB (any SPARQL 1.1 store with a statements endpoint works).

---

## Repository layout

```
.
├── README.md
├── requirements.txt
├── ontology/
│   └── tbox.ttl                     classes and properties, with their metadata sources
├── scripts/
│   ├── build_scope.py               counting scope: 34 applications + dependency closure
│   ├── extract_metadata.py          Odoo metadata -> out/abox.ttl
│   ├── structural_signatures.py     code-side candidates -> out/signatures.ttl, out/candidates.json
│   ├── measure_mean_ftr.py          mean FTR of report EOs and wizard EIs -> data/calib_ftr.ttl
│   └── ai/
│       ├── RUBRIC.md                judging protocol given to the model
│       ├── make_packets.py          one judgement packet per candidate
│       ├── make_batches.py          optional: paste-ready batches for manual judging
│       ├── run_api.py               judge the packets through an LLM API
│       ├── parse_verdicts.py        validate the verdicts -> data/verdicts.ttl
│       └── aggregate_params.py      verdicts -> data/calib_params.ttl (p, m)
├── sparql/
│   ├── update/                      materialisation, run in name order
│   │   ├── 1_maintainers.ru
│   │   ├── 2_clear_functions.ru
│   │   ├── 3_data_functions.ru
│   │   ├── 4_transaction_functions.ru
│   │   └── 5_code_side_estimates.ru
│   └── query/
│       ├── S1.rq ... S10.rq         totals, distributions and assertions
│       ├── rq1_module_ufp.rq        UFP per application module
│       └── trace_functions.rq       ledger: every function of one module with DET/RET/FTR names
├── run/
│   ├── run_updates.ps1              Windows PowerShell runner
│   └── run_updates.sh               Linux / macOS runner
├── data/
│   ├── scope.txt                    the 82 modules of the counting scope
│   ├── develop_set.txt              the 8 development-set modules
│   ├── verdicts.ttl                 AI verdicts on the development-set candidates
│   ├── calib_params.ttl             code-side parameters p, m (from verdicts.ttl)
│   └── calib_ftr.ttl                mean FTR of report EOs and wizard EIs
└── expected/
    └── rq1_expected.csv             expected UFP per application module
```

Generated files go to `out/` (ignored by git).

---

## Requirements

- Python 3.10 or later
  ```
  pip install -r requirements.txt
  ```
  `lxml` is required: view inheritance uses XPath functions such as `hasclass()` that
  only lxml evaluates. For `scripts/ai/run_api.py` also install `anthropic` or `openai`.
- The **Odoo 19.0 Community source tree** (the directory that contains `addons/` and
  `odoo/addons/`). Below it is called `ODOO_ROOT`.
- **GraphDB** (Free edition is sufficient) with one repository, here called `odoo_fsm`,
  created with the ruleset **"No inference"**. With inference enabled, superclass types
  are added to the function instances and the totals become wrong (assertion S8 detects it).

All commands are run from the repository root. Lines ending in `\` continue on the next
line (in Windows PowerShell use a backtick `` ` `` or write the command on one line).

---

## Quick start

Uses the parameters shipped in `data/` (no AI calls).

```bash
# 1. extraction and code-side candidates
python scripts/extract_metadata.py --addons ODOO_ROOT/addons ODOO_ROOT/odoo/addons \
       --scope-file data/scope.txt --restrict --ttl --out out
python scripts/structural_signatures.py --odoo ODOO_ROOT --abox out/abox.ttl \
       --out-ttl out/signatures.ttl --out-cand out/candidates.json

# 2. load five files into the default graph of the repository (see step 6 below)
#    ontology/tbox.ttl  out/abox.ttl  out/signatures.ttl  data/calib_params.ttl  data/calib_ftr.ttl

# 3. materialise
bash run/run_updates.sh                                   # Linux / macOS
powershell -ExecutionPolicy Bypass -File .\run\run_updates.ps1   # Windows

# 4. run sparql/query/S1.rq ... S10.rq and rq1_module_ufp.rq; compare with "Expected results"
```

---

## Full procedure

### 1. Counting scope

```bash
python scripts/build_scope.py --addons ODOO_ROOT/addons ODOO_ROOT/odoo/addons --out data/scope.txt
```

The scope is the 34 applications and the transitive closure of their manifest
dependencies: 82 modules. Auto-installed extension modules are not included, so the Odoo
installation used for any manual counting must contain exactly these modules (Odoo
installs auto-install modules silently; uninstall them and check again after installing
anything).

### 2. Metadata extraction

```bash
python scripts/extract_metadata.py --addons ODOO_ROOT/addons ODOO_ROOT/odoo/addons \
       --scope-file data/scope.txt --restrict --ttl --out out
```

Expected console output (Odoo 19.0 Community):

```
scope modules loaded: 82
delegated (_inherits) fields injected: 858
report output fields marked visible: 53
transactions: window 745 / report 66 / server 98 / menu targets 449 / buttons 1393 / templates 1110
```

Outputs: `out/abox.ttl` and `out/warnings.log`. About 15 `PATCHMISS` lines in the log
are normal; several hundred mean that lxml is missing.

### 3. Code-side candidates

```bash
python scripts/structural_signatures.py --odoo ODOO_ROOT --abox out/abox.ttl \
       --out-ttl out/signatures.ttl --out-cand out/candidates.json
```

Expected: `[out] {'button': 596, 'server': 98, 'route': 552}`.
Buttons are attributed to the module that first defines the called method, routes to
the module of their controller, server actions to their declaring module.

### 4. Mean FTR of report EOs and wizard EIs

```bash
python scripts/measure_mean_ftr.py --abox out/abox.ttl --develop-set data/develop_set.txt \
       --out data/calib_ftr.ttl
```

Expected: `eo_report : n=28 meanFtr=3.571` and `wizard : n=17 meanFtr=2.412`.
The measurement is static (no AI): a report reads its model and every table traversed
by its resolvable output paths; a wizard writes the tables targeted by its relational
fields. The rules round the means (4 and 2).

### 5. Code-side parameters from AI verdicts

`data/verdicts.ttl` and `data/calib_params.ttl` are included. To recompute the parameters
from the verdicts:

```bash
python scripts/ai/aggregate_params.py --verdicts data/verdicts.ttl --out data/calib_params.ttl
```

Expected:

| entry  | cat | judged | admitted | p      | m     |
|--------|-----|--------|----------|--------|-------|
| button | EI  | 243    | 54       | 0.2222 | 3.519 |
| button | EO  | 243    | 2        | 0.0082 | 4.000 |
| route  | EI  | 52     | 20       | 0.3846 | 3.450 |
| route  | EO  | 52     | 15       | 0.2885 | 4.733 |
| route  | EQ  | 52     | 9        | 0.1731 | 3.111 |
| server | EI  | 51     | 10       | 0.1961 | 3.000 |

`p` = admitted candidates of the category / judged candidates of the entry kind;
`m` = mean UFP of the admitted candidates, rated with the IFPUG matrix from the DET
fields and FTR tables listed in each verdict.

To judge the candidates again (every candidate of the development set must be judged,
because the judged candidates are the denominator of p):

```bash
# a) one packet per development-set candidate (structural context + code slice + rubric)
python scripts/ai/make_packets.py --cand out/candidates.json --odoo ODOO_ROOT \
       --abox out/abox.ttl out/signatures.ttl --rubric scripts/ai/RUBRIC.md \
       --outdir out/packets --only-modules hr,fleet,mass_mailing,survey,point_of_sale,crm,stock,mrp

# b) judge them: either through an API (each request is an independent context) ...
export ANTHROPIC_API_KEY=...          # or OPENAI_API_KEY with --provider openai [--base-url ...]
python scripts/ai/run_api.py --packets out/packets --cand out/candidates.json \
       --out out/verdicts.jsonl --only-modules hr,fleet,mass_mailing,survey,point_of_sale,crm,stock,mrp
#    ... or manually: build batches, paste each into a fresh chat, append the JSON lines to out/verdicts.jsonl
python scripts/ai/make_batches.py --packets out/packets --outdir out/batches --per-batch 20

# c) validate and convert, then aggregate
python scripts/ai/parse_verdicts.py --jsonl out/verdicts.jsonl --cand out/candidates.json \
       --out data/verdicts.ttl
python scripts/ai/aggregate_params.py --verdicts data/verdicts.ttl --out data/calib_params.ttl
```

`run_api.py` is resumable: candidates already present in `--out` are skipped.
The judging protocol is `scripts/ai/RUBRIC.md`.

### 6. Load the data into GraphDB

Create the repository `odoo_fsm` (ruleset "No inference"). Then import five files into the
**default graph**, either in the Workbench (Import > Upload RDF files, target graph:
"The default graph") or from the command line.

Linux / macOS:

```bash
EP=http://localhost:7200/repositories/odoo_fsm/statements
curl -X POST -H "Content-Type: application/sparql-update" --data "CLEAR ALL" $EP
for f in ontology/tbox.ttl out/abox.ttl out/signatures.ttl data/calib_params.ttl data/calib_ftr.ttl; do
  curl -X POST -H "Content-Type: text/turtle" --data-binary @"$f" $EP && echo "imported $f"
done
```

Windows PowerShell:

```powershell
$EP = "http://localhost:7200/repositories/odoo_fsm/statements"
Invoke-RestMethod -Uri $EP -Method Post -ContentType "application/sparql-update" -Body "CLEAR ALL"
foreach ($f in "ontology\tbox.ttl","out\abox.ttl","out\signatures.ttl","data\calib_params.ttl","data\calib_ftr.ttl") {
  Invoke-RestMethod -Uri $EP -Method Post -ContentType "text/turtle" -InFile $f -TimeoutSec 3600; Write-Host "imported $f" }
```

### 7. Materialise the functions

```bash
bash run/run_updates.sh                                   # Linux / macOS
powershell -ExecutionPolicy Bypass -File .\run\run_updates.ps1   # Windows
```

Both accept another statements endpoint as argument (`-Endpoint ...` in PowerShell).
The five updates run in name order and print one `OK` line each; on error the runner
stops and prints the server's message.

| Update | Effect |
|---|---|
| `1_maintainers.ru` | derives `:maintainedBy` (an application maintains a table of another module through a reachable form allowing creation) |
| `2_clear_functions.ru` | clears the graph `<urn:functions>` |
| `3_data_functions.ru` | ILF and EIF per module |
| `4_transaction_functions.ru` | maintenance EIs, wizard EIs, EQs, report EOs, analysis EOs |
| `5_code_side_estimates.ru` | code-side estimate groups: n × p × m |

After any change to the extraction, the signatures or the parameters, replace the data
in the repository and run the whole sequence again. Do not rerun a single update: an
instance would then carry the values of two runs.

### 8. Queries

Run the files of `sparql/query/` in the Workbench (SPARQL tab), or from the command line:

```bash
curl -H "Accept: text/csv" --data-urlencode query@sparql/query/S9.rq \
     http://localhost:7200/repositories/odoo_fsm
```

| Query | Result |
|---|---|
| `S1.rq` | UFP and rows by counting side (declarative, parameterized) |
| `S2.rq` | instances and UFP by IFPUG type (module level) |
| `S3.rq` | UFP per application module and rule branch, attributed to the host module |
| `S4.rq` | every function instance with DET, RET, FTR, complexity and UFP |
| `S5.rq` | instances and UFP by type and complexity (module level) |
| `S6.rq` | UFP per declaring module and rule branch |
| `S7.rq` | assertion: every transaction has a declarer |
| `S8.rq` | assertion: no inferred superclass types (0) |
| `S9.rq` | system level: the 34 applications as one system |
| `S10.rq` | system level by type and complexity (total equals S9) |
| `rq1_module_ufp.rq` | per application module: A (declarative) + B (button, route, server) = UFP |
| `trace_functions.rq` | ledger of one module: change `VALUES ?mod { :module_sale_management }` |

---

## Expected results (Odoo 19.0 Community, 82-module scope)

| Check | Expected |
|---|---|
| Parameters | `CalibrationStat`: 8 cells (6 with p and m, 2 with meanFtr) |
| S1 | declarative 10668 UFP / 2084 rows; parameterized 1719 UFP / 142 rows |
| S7 | EI 828 / 828, EO 171 / 171, EQ 319 / 319 |
| S8 | 0 |
| S9 | 1 ILF 239 / 1715; 2 EIF 35 / 175; 3 transactions 905 / 4159; **4 total 1179 / 6049** |
| RQ1 | identical to `expected/rq1_expected.csv` |

---

## Counting rules in brief

**Data functions**
- A persistent table is user-recognizable when it has a visible field (in a view,
  including search views, or in a report output) or a reachable window action.
- ILF: counted for its host module and for every application that maintains it.
  DET = visible stored fields (relational fields counted once per target table;
  related, non-stored computed and one2many/many2many fields excluded; the foreign key of
  a subgroup to its parent excluded). RET = 1 + subgroups.
- Subgroup (`:foldedInto`): a table reached only through its parent: delegation
  (`_inherits`) or a required one2many inverse with cascade deletion.
- EIF: a table the module references (relational field or view FTR) but neither hosts
  nor maintains. DET = 1 + fields exposed through related fields.
- System level (S9, S10): data functions deduplicated by table; a table that is an ILF of
  any application is an ILF of the system; an EIF only otherwise.

**Transaction functions** (only reachable actions: menu, button of type action, binding)
- View selection follows Odoo: the view types the action provides; for each type the
  per-type binding, then `view_id`, then `<type>_view_ref` in the context, then the default
  view with the lowest priority.
- Maintenance EIs per table and form view: create, write, delete, each allowed by the form
  (`create`, `edit`, `delete`). DET = form fields + 2 (action, message); buttons are not
  DETs. Create excludes fields necessarily hidden when creating (invisible condition on
  `id` only), write and delete exclude those hidden when editing. FTR = tables referenced by
  the form; delete EIs have FTR 1.
- EQ: one per table and list field set. EO: one per report (DET = deduplicated template
  outputs + 2) and one per pivot or graph view.
- Wizard EI: one per transient model with a reachable form; FTR = rounded mean (2).
- Report EO FTR = rounded mean (4).
- Transactions are attributed to the module declaring the action (`:declaredBy`).

**Code side**: per module and entry kind, n candidates × p × m for each category.

**Not counted**: overrides of create/write/unlink in Python (they belong to the
maintenance EI already counted), scheduled actions, website menu entries, generic platform
operations (export, duplicate, import), fields shown only by client widgets.

### Rule branches (`:byRule`)

| Branch | Function |
|---|---|
| `1_ILF`, `2_EIF` | data functions |
| `3_CRUD_c`, `3_CRUD_w`, `3_CRUD_u` | maintenance EI: create, write, delete |
| `4_EQ` | list view |
| `5_EO_report` | printed report |
| `6_EO_analysis` | pivot or graph view |
| `7_Wizard_EI` | wizard |
| `8_<kind>_<category>` | code-side estimate, e.g. `8_route_EI` |

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| S1 has only the `declarative` row | the code-side parameters are missing: import `data/calib_params.ttl` (class `CalibrationStat`, properties `pValue`, `mValue`) and run the updates again |
| S8 is not 0 | the repository uses inference; recreate it with the ruleset "No inference" |
| Totals larger than expected | data from an earlier run is still in the repository; `CLEAR ALL`, import the five files, run all updates |
| Many `PATCHMISS` lines in `warnings.log` | lxml is not installed |
| Module count in the graph is not 80 | the extraction did not use `data/scope.txt` (two of the 82 modules declare no models) |
