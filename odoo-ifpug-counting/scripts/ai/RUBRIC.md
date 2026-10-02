# RUBRIC v2 — per-item adjudication protocol
(Verbatim in every packet. Judge each artifact independently, with no other
context. Output ONE JSON line per artifact -- exactly one line if this is a
single packet, one line per ITEM if this is a batch. Schema at the end.)

## Your task
You are given ONE code artifact from the Odoo ERP (a button method, a
server-action code block, or an HTTP route handler) plus minimal
structural context. Decide whether it constitutes an IFPUG-countable
elementary transaction, and if so, measure it.

## Decision rules

1. **verdict = yes** iff the artifact, when triggered, performs a
   self-contained elementary process meaningful to a business user:
   it stores/changes/deletes business data (EI), retrieves and presents
   stored data as-is (EQ), or derives/aggregates data for output (EO).

2. **verdict = no** with `no_reason`:
   - `forward`  — it only opens another window/wizard/action or returns an
     act_window dict (the target is counted elsewhere; counting this too
     would double-count);
   - `technical` — logging, cache, cron plumbing, recomputation, migration,
     purely internal state with no user-recognizable outcome;
   - `unreachable` — dead code / raises immediately / feature-flagged off;
   - `duplicate` — a thin alias of another entry point in the same module.

3. **category** (only if yes): EI if its primary intent changes stored data;
   EQ if it retrieves without derivation; EO if it derives, aggregates,
   renders a report/page with computed content. Pick the primary intent.

4. **det_fields** — list the elementary data elements the USER supplies or
   receives in this transaction: field names read from the request/record
   and written as its outcome. Rules:
   - use model field names where visible (e.g. "state", "partner_id");
   - a computed message/notification counts as one element ("_message");
   - do NOT count loop variables, context keys, or technical ids;
   - if the body gives no visibility (e.g. pure super() call), return [].

5. **ftr_tables** — list every model/table this transaction MATERIALLY reads
   or writes (host model included when touched). "Materially" = the data
   read/written shapes the user-visible outcome. Exclude: logging tables,
   ir.* plumbing, mail.followers-style side effects, attachments unless the
   transaction is about them.

6. **confidence**: high (clear), med (some inference), low (body not fully
   visible / dynamic dispatch). When locator is UNRESOLVED you judge from
   the method name + model semantics: cap confidence at med.

## Output schema (ONE line of JSON, nothing else)
{"id":"<packet id>","verdict":"yes|no","no_reason":null|"forward|technical|unreachable|duplicate",
 "category":null|"EI|EO|EQ","det_fields":[...],"ftr_tables":[...],
 "confidence":"high|med|low","reasons":"<= 30 words"}

## Calibration examples
- Button `action_confirm` on stock.picking that validates moves and writes
  quantities → yes, EI, det ["state","date_done","move_ids/quantity"],
  ftr ["stock.picking","stock.move"], high.
- Button that returns an act_window opening a wizard → no, forward.
- Server action code `records.write({'active': False})` → yes, EI,
  det ["active"], ftr [host model], high.
- Route rendering a portal page of the user's orders → yes, EQ (or EO if it
  aggregates totals), det = displayed fields, ftr = models queried.
