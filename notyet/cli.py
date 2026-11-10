"""Command line: ``not-yet bench | show | tbox | evolve | list | llm``."""
from __future__ import annotations

import argparse
import sys

from .agents import PROFILES
from .defenses import registry
from .evolve import evolve
from .harness import episodes, prepare, run, score, split
from .ontology import reference_tbox, seed_tbox
from .suites import SUITES

MARK = {"ready": "ready    ", "waiting": "WAITING  ", "forbidden": "FORBIDDEN"}


def table(rows):
    """rows: name -> score dict."""
    out = ["| defense | clean utility | utility under attack | attack success (all) | action attacks | "
           "answer-only attacks |", "|---|---:|---:|---:|---:|---:|"]
    for name, s in rows.items():
        out.append(f"| `{name}` | {s['cu']:.3f} | {s['au']:.3f} | {s['asr']:.3f} | {s['asr_actions']:.3f} | "
                   f"{s['asr_by_kind']['answer']:.3f} |")
    return "\n".join(out)


def _short(v, n=48):
    r = repr(v)
    return r if len(r) <= n else r[:n - 5] + "...'" + ("]" if isinstance(v, list) else "")


def _find(task_id):
    for s in SUITES.values():
        for t in s.user_tasks:
            if t.id == task_id or t.id.endswith("/" + task_id):
                return s, t
    sys.exit(f"no task {task_id!r}; try `not-yet list`")


def _evolved(seed=42, min_support=2):
    sp = split(seed)
    return evolve(sp["train"], sp["val"], min_support=min_support).tbox


def cmd_list(a):
    for s in SUITES.values():
        print(f"## {s.name}")
        for t in s.user_tasks:
            print(f"  {t.id:16s} {t.prompt}")
            for i in t.intents:
                print(f"  {'':16s}   authorises {i}")
        for i in s.injection_tasks:
            print(f"  {i.id:16s} [{i.kind}] {i.goal}")


def cmd_show(a):
    suite, task = _find(a.task)
    inj = None
    if a.injection:
        inj = next((i for i in suite.injection_tasks if i.id == a.injection or i.id.endswith("/" + a.injection)),
                   None) or sys.exit(f"no injection {a.injection!r} in {suite.name}")
    reg = registry(_evolved() if a.defense.startswith("gard-evolved") else None)
    if a.defense not in reg:
        sys.exit(f"unknown defense; choose from {', '.join(reg)}")
    e = run(suite, task, inj, PROFILES[a.profile], reg[a.defense], a.defense)
    print(f"task      {task.id}: {task.prompt}")
    for i in task.intents:
        print(f"intent    {i}")
    if inj:
        print(f"injection {inj.id} [{inj.kind}]: {inj.goal}")
    print(f"agent     {a.profile} (gullible)   defense {a.defense}\n")
    for k, s in enumerate(e.trace, 1):
        args = ", ".join(f"{k2}={_short(v)}" for k2, v in s.args.items())
        tag = "" if s.origin == "plan" else f"  <- {s.origin}"
        print(f"{k:2d}. {MARK[s.verdict]} {s.tool}({args}){tag}")
        if s.verdict != "ready" or s.tool in suite.tools and suite.tools[s.tool].state_changing:
            print(f"    {'':9s} {s.reason}")
    print(f"\nanswer    {e.answer!r}")
    print(f"utility   {'yes' if e.utility else 'NO'}" + (f"    attacker succeeded: {'YES' if e.success else 'no'}"
                                                         if inj else ""))


def cmd_tbox(a):
    tb = {"seed": seed_tbox, "reference": reference_tbox}.get(a.which, lambda: _evolved())()
    if a.dot:
        print("digraph tbox {\n  rankdir=LR; node [shape=box, fontname=Helvetica];")
        for ax in tb:
            if ax.rel in ("requires", "observes"):
                style = "solid" if ax.rel == "requires" else "dashed"
                print(f'  "{ax.action}" -> "{ax.state}" [label="{ax.rel}", style={style}];')
        print("}")
        return
    for ax in tb:
        print(ax)


def cmd_evolve(a):
    sp = split(a.seed)
    print(f"split seed {a.seed}: train {sp['train']}\n            val   {sp['val']}\n")
    r = evolve(sp["train"], sp["val"], min_support=a.min_support)
    for k, (edit, before, after) in enumerate(r.rounds, 1):
        print(f"accepted {k}: {edit}   [{edit.origin}] training failures {before} -> {after}")
    print()
    for key, (edit, why) in sorted(r.rejected.items()):
        print(f"rejected  {key}   [{edit.origin}]\n          {why}")
    print("\nfrozen TBox obligations:", ", ".join(str(x) for x in r.tbox if x.rel == "requires") or "none")


def cmd_bench(a):
    sp = split(a.seed)
    tasks = None if a.split == "all" else set(sp[a.split])
    reg = registry(_evolved(a.seed, a.min_support))
    rows = {}
    for name, f in reg.items():
        prof = [PROFILES[a.profile]] if a.profile else None
        rows[name] = score(episodes(f, name, tasks=tasks, profiles=prof))
    n = next(iter(rows.values()))
    print(f"{a.split} split: {n['clean']} clean and {n['attacked']} attacked episodes\n")
    print(table(rows))


def cmd_llm(a):  # pragma: no cover - needs a live endpoint
    from .llm import http_transport, run_llm
    send = http_transport(a.base_url, a.model, a.api_key)
    reg = registry(_evolved())
    rows = {}
    for name in a.defenses:
        eps = []
        for suite in SUITES.values():
            for task in suite.user_tasks:
                if a.tasks and task.id not in a.tasks:
                    continue
                eps.append(run_llm(suite, task, None, reg[name], send, name=a.model))
                for inj in suite.injection_tasks:
                    eps.append(run_llm(suite, task, inj, reg[name], send, name=a.model))
        rows[name] = score([e for e in eps if e.injection is None or e.attacked])
        print(f"{name}: {rows[name]}", flush=True)
    print(table(rows))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="not-yet", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="user tasks, what they authorise, and injection tasks").set_defaults(fn=cmd_list)
    p = sub.add_parser("show", help="replay one episode and explain every decision")
    p.add_argument("task")
    p.add_argument("--injection", "-i")
    p.add_argument("--profile", "-p", default="hasty", choices=sorted(PROFILES))
    p.add_argument("--defense", "-d", default="gard-reference")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("tbox", help="print a TBox (or a Graphviz graph of it)")
    p.add_argument("which", nargs="?", default="reference", choices=["seed", "reference", "evolved"])
    p.add_argument("--dot", action="store_true")
    p.set_defaults(fn=cmd_tbox)
    p = sub.add_parser("evolve", help="run offline ontology evolution and show every accept / reject")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--min-support", type=int, default=2)
    p.set_defaults(fn=cmd_evolve)
    p = sub.add_parser("bench", help="score every defense")
    p.add_argument("--split", default="all", choices=["all", "train", "val", "test"])
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--min-support", type=int, default=2)
    p.add_argument("--profile", choices=sorted(PROFILES))
    p.set_defaults(fn=cmd_bench)
    p = sub.add_parser("llm", help="use a model behind an OpenAI-compatible endpoint as the agent")
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--api-key", default="")
    p.add_argument("--defenses", nargs="+", default=["none", "gard-reference"])
    p.add_argument("--tasks", nargs="*")
    p.set_defaults(fn=cmd_llm)
    a = ap.parse_args(argv)
    prepare()
    a.fn(a)


if __name__ == "__main__":
    main()
