#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_api.py — run the judgement packets through an LLM API.

Works with Anthropic and with any OpenAI-compatible endpoint (OpenAI itself,
Azure-style gateways, local vLLM/Ollama, domestic providers). Every API
request is a stateless, independent context: this IS the "one fresh session
per packet" protocol, executed automatically.

Setup (pick one):
    pip install anthropic     &&  setx ANTHROPIC_API_KEY sk-ant-...
    pip install openai        &&  setx OPENAI_API_KEY sk-...
(Windows: reopen the shell after setx.)

Usage:
    # every development-set candidate must be judged (it is the denominator of p)
    python scripts/ai/run_api.py --packets out/packets --cand out/candidates.json \\
           --out out/verdicts.jsonl --only-modules hr,fleet,mass_mailing,survey,point_of_sale,crm,stock,mrp

Resumable: already-judged ids in --out are skipped, so you can stop and rerun.
"""
import argparse, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed


def extract_json(text):
    """Pull the verdict object out of a reply that may contain thinking text,
    markdown fences, or several JSON-looking blobs."""
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S)
    text = re.sub(r"<thinking>.*?</thinking>", " ", text, flags=re.S)
    text = re.sub(r"```[a-zA-Z]*", " ", text).replace("```", " ")
    objs, depth, start, instr, esc = [], 0, None, False, False
    for i, ch in enumerate(text):
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
            continue
        if ch == '"':
            instr = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start is not None:
                    objs.append(text[start:i + 1])
                    start = None
    for s in reversed(objs):
        try:
            v = json.loads(s)
        except Exception:
            continue
        if isinstance(v, dict) and v.get("verdict") in ("yes", "no"):
            return v
    raise ValueError("no verdict JSON in reply")


SYSTEM = ("You are an IFPUG function point counting specialist. You judge ONE "
          "code artifact at a time, strictly following the rubric in the user "
          "message. Reply with exactly ONE line of JSON and nothing else.")


def load_done(path):
    done = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except Exception:
                continue
            if v.get("verdict") not in ("yes", "no"):
                continue                       # malformed -> judge again
            if str(v.get("reasons", "")).startswith("API_ERROR"):
                continue                       # failed -> judge again
            done.add(v["id"])
    return done


def make_client(provider, base_url, api_key_env, max_tokens=900, extra=None):
    """Returns (client, call) where call(model, text) -> reply text."""
    key = os.environ.get(api_key_env) if api_key_env else None
    if provider == "anthropic":
        try:
            import anthropic
        except ImportError:
            sys.exit("pip install anthropic")
        kw = {}
        if key:
            kw["api_key"] = key
        if base_url:
            kw["base_url"] = base_url
        cl = anthropic.Anthropic(**kw)

        def call(model, text):
            kw2 = dict(model=model, max_tokens=max_tokens, system=SYSTEM,
                       messages=[{"role": "user", "content": text}])
            if extra:
                kw2["extra_body"] = extra
            msg = cl.messages.create(**kw2)
            return "".join(b.text for b in msg.content if b.type == "text")
        return cl, call

    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("pip install openai")
    kw = {}
    if key:
        kw["api_key"] = key
    if base_url:
        kw["base_url"] = base_url
    cl = OpenAI(**kw)

    def call(model, text):
        kw2 = dict(model=model, max_tokens=max_tokens,
                   messages=[{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": text}])
        if extra:
            kw2["extra_body"] = extra
        r = cl.chat.completions.create(**kw2)
        msg = r.choices[0].message
        txt = msg.content or ""
        if not txt:                      # some providers put it in reasoning_content
            txt = getattr(msg, "reasoning_content", "") or ""
        return txt
    return cl, call


def judge(call, model, cid, text, retries=3):
    for attempt in range(retries):
        try:
            v = extract_json(call(model, text))
            v["id"] = cid                      # trust the filename, not the model
            v["model"] = model
            return v
        except Exception as e:
            if attempt == retries - 1:
                return {"id": cid, "verdict": "no", "no_reason": "technical",
                        "category": None, "det_fields": [], "ftr_tables": [],
                        "confidence": "low", "reasons": f"API_ERROR {e}"[:150]}
            time.sleep(2 * (attempt + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--packets", required=True)
    ap.add_argument("--cand", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--provider", choices=["anthropic", "openai"],
                    default="anthropic",
                    help="'openai' also covers any OpenAI-compatible endpoint "
                         "via --base-url (vLLM, Ollama, gateways)")
    ap.add_argument("--model", help="default: claude-sonnet-4-6 / gpt-4o")
    ap.add_argument("--base-url", help="custom endpoint for OpenAI-compatible APIs")
    ap.add_argument("--api-key-env", help="env var holding the key "
                    "(default ANTHROPIC_API_KEY / OPENAI_API_KEY)")
    ap.add_argument("--max-tokens", type=int, default=900,
                    help="output budget; raise if replies get truncated")
    ap.add_argument("--disable-thinking", action="store_true",
                    help="MiniMax M3 and similar: turn reasoning off so the "
                         "output budget is spent on the JSON")
    ap.add_argument("--run-id", help="batch label recorded on every verdict "
                    "(provenance: which model/batch judged this item)")
    ap.add_argument("--list-models", action="store_true",
                    help="print the model ids your account can use, then exit")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only-modules")
    ap.add_argument("--skip-modules")
    ap.add_argument("--kinds", default="button,route,server", help="comma list of entry kinds (default: button,route,server)")
    ap.add_argument("--sample", type=int, help="random subsample of the selection")
    ap.add_argument("--limit", type=int, help="stop after N (smoke test)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    key_env = args.api_key_env or ("ANTHROPIC_API_KEY"
                if args.provider == "anthropic" else "OPENAI_API_KEY")
    if args.list_models:
        cl, _ = make_client(args.provider, args.base_url, key_env)
        try:
            rows = []
            for m in cl.models.list():
                mid = getattr(m, "id", None) or (m[0] if isinstance(m, tuple) else str(m))
                ts = getattr(m, "created_at", None) or getattr(m, "created", None)
                key = 0
                shown = ""
                if ts is not None:
                    try:
                        import datetime as _dt
                        if isinstance(ts, (int, float)):
                            key = float(ts)
                            shown = _dt.datetime.utcfromtimestamp(key).strftime("%Y-%m-%d")
                        else:
                            shown = str(ts)[:10]
                            key = _dt.datetime.fromisoformat(
                                str(ts).replace("Z", "+00:00")).timestamp()
                    except Exception:
                        shown = str(ts)[:19]
                rows.append((key, str(mid), shown))
            rows.sort(reverse=True)          # newest first
            print(f"[models] {len(rows)} available (newest first):")
            for _k, mid, shown in rows:
                print(f"  {mid:45s} {shown}")
            print("\nPin one explicitly:  --model <id>   "
                  "(the script never auto-selects; a fixed id keeps the run reproducible)")
        except Exception as e:
            print("could not list models:", e)
            print("check the provider docs / console for current model ids")
        return

    cands = json.load(open(args.cand, encoding="utf-8"))
    only = set(args.only_modules.split(",")) if args.only_modules else None
    skip = set(args.skip_modules.split(",")) if args.skip_modules else None
    kinds = set(args.kinds.split(","))
    sel = [c for c in cands
           if (only is None or c.get("module") in only)
           and (skip is None or c.get("module") not in skip)
           and (kinds is None or c["kind"] in kinds)]
    if args.sample and args.sample < len(sel):
        import random
        random.Random(args.seed).shuffle(sel)
        sel = sel[:args.sample]
    done = load_done(args.out)
    todo = [c for c in sel if c["id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    if not args.model:
        args.model = ("claude-sonnet-4-6" if args.provider == "anthropic"
                      else "gpt-4o")
    print(f"[plan] selected {len(sel)}, to judge now {len(todo)}, "
          f"provider {args.provider}, model {args.model}, "
          f"endpoint {args.base_url or 'default'}, workers {args.workers}")
    if not todo:
        return

    extra = {"thinking": {"type": "disabled"}} if args.disable_thinking else None
    _cl, call = make_client(args.provider, args.base_url, key_env,
                            args.max_tokens, extra)
    fh = open(args.out, "a", encoding="utf-8")
    n_ok = n_err = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for c in todo:
            p = os.path.join(args.packets, c["id"] + ".md")
            if not os.path.exists(p):
                print(f"  ! packet missing: {p}")
                continue
            txt = open(p, encoding="utf-8").read()
            futs[ex.submit(judge, call, args.model, c["id"], txt)] = c["id"]
        for i, f in enumerate(as_completed(futs), 1):
            v = f.result()
            if args.run_id:
                v["run_id"] = args.run_id
            fh.write(json.dumps(v, ensure_ascii=False) + "\n")
            fh.flush()
            if str(v.get("reasons", "")).startswith("API_ERROR"):
                n_err += 1
            else:
                n_ok += 1
            if i % 25 == 0 or i == len(futs):
                print(f"  {i}/{len(futs)}  ok={n_ok} err={n_err}")
    fh.close()
    print(f"[done] appended to {args.out}. Next: parse_verdicts.py")


if __name__ == "__main__":
    main()
