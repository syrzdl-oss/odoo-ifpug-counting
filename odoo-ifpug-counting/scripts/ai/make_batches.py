#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_batches.py — bundle packets into paste-ready batch files.

For the manual route (ChatGPT / Claude subscription, no API): each batch file
carries the rubric ONCE plus N items, and asks for N JSON lines back. Paste one
batch per fresh chat session, paste the reply lines into verdicts.jsonl.

Usage:
  python scripts/ai/make_batches.py --packets out/packets --outdir out/batches --per-batch 20
  python scripts/ai/make_batches.py --packets out/packets --outdir out/batches \\
         --per-batch 25 --max-chars 45000 --group-by-kind

Notes:
  * Items are read from the .md packets produced by make_packets.py;
    the per-item rubric copy is stripped and emitted once at the top.
  * --max-chars caps a batch even if --per-batch is not reached (one oversized
    item always gets its own batch).
  * INDEX.tsv records which ids landed in which batch.
"""
import argparse, os, re, random

MARK = "# RUBRIC v2"

HEAD = """# JUDGEMENT BATCH {n} — {count} items

You are an IFPUG function point counting specialist. Below you find ONE shared
rubric and then {count} independent code artifacts, each marked `## ITEM <id>`.

Judge every item **independently of the others** — do not let one item's
verdict influence another. Work through them in order.

**Output format — this matters:** reply with exactly {count} lines of JSON,
one line per item, in the same order, no commentary, no markdown fences,
no blank lines. Each line must carry the item's own `id` exactly as given.

---

{rubric}

---

# ITEMS ({count})

"""

TAIL = """
---

# REMINDER

Reply now with exactly {count} JSON lines, one per item, ids in this order:
{ids}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--packets", required=True)
    ap.add_argument("--outdir", default="batches")
    ap.add_argument("--per-batch", type=int, default=20)
    ap.add_argument("--max-chars", type=int, default=45000)
    ap.add_argument("--group-by-kind", action="store_true",
                    help="keep each batch to a single candidate kind")
    ap.add_argument("--shuffle-seed", type=int,
                    help="shuffle items before batching (reduces module-order effects)")
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(args.packets) if f.endswith(".md"))
    items, rubric = [], None
    for f in files:
        s = open(os.path.join(args.packets, f), encoding="utf-8", errors="replace").read()
        i = s.find(MARK)
        body = s[:i].rstrip() if i > 0 else s.rstrip()
        if rubric is None and i > 0:
            rubric = s[i:].strip()
        cid = f[:-3]
        kind = "other"
        m = re.search(r"kind:\s*(\S+)", body)
        if m:
            kind = m.group(1)
        items.append((cid, kind, body))
    if rubric is None:
        raise SystemExit("no rubric found inside the packets")
    print(f"[in] {len(items)} packets, rubric {len(rubric)} chars")

    if args.shuffle_seed is not None:
        random.Random(args.shuffle_seed).shuffle(items)
    if args.group_by_kind:
        items.sort(key=lambda x: x[1])

    batches, cur, cur_chars, cur_kind = [], [], 0, None
    for cid, kind, body in items:
        over = (len(cur) >= args.per_batch
                or (cur and cur_chars + len(body) > args.max_chars)
                or (args.group_by_kind and cur and kind != cur_kind))
        if over:
            batches.append(cur)
            cur, cur_chars = [], 0
        cur.append((cid, body))
        cur_chars += len(body)
        cur_kind = kind
    if cur:
        batches.append(cur)

    os.makedirs(args.outdir, exist_ok=True)
    index = []
    for n, b in enumerate(batches, 1):
        ids = [cid for cid, _ in b]
        parts = [HEAD.format(n=n, count=len(b), rubric=rubric)]
        for cid, body in b:
            parts.append(f"## ITEM {cid}\n\n{body}\n")
        parts.append(TAIL.format(count=len(b), ids="\n".join(ids)))
        text = "\n".join(parts)
        path = os.path.join(args.outdir, f"batch_{n:03d}.md")
        open(path, "w", encoding="utf-8").write(text)
        index += [f"batch_{n:03d}\t{cid}" for cid in ids]
        print(f"  batch_{n:03d}.md  {len(b):3d} items  {len(text)//1000:3d}K chars")
    open(os.path.join(args.outdir, "INDEX.tsv"), "w", encoding="utf-8").write(
        "\n".join(index) + "\n")
    print(f"[out] {len(batches)} batches -> {args.outdir}/  (INDEX.tsv maps id -> batch)")
    print("Paste one batch per fresh chat session; append the reply lines to verdicts.jsonl")


if __name__ == "__main__":
    main()
