"""Regenerate results/results.json and results/table.md. ``--check`` fails if they are out of date."""

import argparse
import json
import pathlib
import statistics
import sys

from notyet.agents import PROFILES
from notyet.cli import table
from notyet.defenses import gard, registry
from notyet.evolve import evolve
from notyet.harness import episodes, prepare, score, split
from notyet.ontology import reference_tbox
from notyet.suites import SUITES

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN = ["none", "tool-filter", "taint", "gard-seed", "gard-reference-no-wait", "gard-reference"]
FRACTIONS = [0.2, 0.4, 0.6, 0.8]
SEEDS = range(10)


def r3(x):
    return round(x, 4)


def clean(s):
    return {k: (r3(v) if isinstance(v, float) else {a: r3(b) for a, b in v.items()} if isinstance(v, dict) else v)
            for k, v in s.items()}


def build():
    prepare()
    data = {}
    md = ["# Results", "", "Every number below is produced by `python scripts/results.py` from this repository. "
          "The agents are scripted (see the README), so these numbers describe the defenses' logic on "
          "this harness, not any model.", ""]
    n_tasks = sum(len(s.user_tasks) for s in SUITES.values())
    n_inj = sum(len(s.injection_tasks) for s in SUITES.values())

    # 1. headline over every task, both agent profiles
    reg = registry()
    eps = {name: episodes(reg[name], name) for name in MAIN}
    head = {name: clean(score(e)) for name, e in eps.items()}
    data["headline"] = head
    first = head["none"]
    md += ["## All tasks", "", f"{n_tasks} user tasks, {n_inj} injection tasks, 2 agent profiles: "
           f"{first['clean']} clean episodes and {first['attacked']} attacked episodes (pairs where the injection "
           "reaches the agent).", "",
           table(head), ""]

    # 2. per profile
    prof = {}
    for name in MAIN:
        prof[name] = {p: clean(score([e for e in eps[name] if e.profile == p])) for p in PROFILES}
    data["by_profile"] = prof
    md += ["## Clean utility and utility under attack by agent profile", "",
           "| defense | diligent CU | hasty CU | diligent AU | hasty AU |", "|---|---:|---:|---:|---:|"]
    for name in MAIN:
        d, h = prof[name]["diligent"], prof[name]["hasty"]
        md.append(f"| `{name}` | {d['cu']:.3f} | {h['cu']:.3f} | {d['au']:.3f} | {h['au']:.3f} |")
    md.append("")

    # 3. per injection task
    inj = {}
    for s in SUITES.values():
        for i in s.injection_tasks:
            inj[i.id] = {"kind": i.kind, "goal": i.goal}
            for name in MAIN:
                xs = [e for e in eps[name] if e.injection == i.id]
                inj[i.id][name] = r3(sum(e.success for e in xs) / len(xs)) if xs else None
    data["by_injection"] = inj
    md += ["## Attack success by injection task", "",
           "| injection | kind | " + " | ".join(f"`{n}`" for n in MAIN) + " |", "|---|---|" + "---:|" * len(MAIN)]
    for k, v in inj.items():
        md.append(f"| `{k}` | {v['kind']} | " + " | ".join(f"{v[n]:.2f}" for n in MAIN) + " |")
    md.append("")

    # 4. evolution, paper split (40/10/50, seed 42)
    sp = split(42)
    rep = evolve(sp["train"], sp["val"])
    test = set(sp["test"])
    reg = registry(rep.tbox)
    evo = {name: clean(score(episodes(reg[name], name, tasks=test))) for name in reg}
    data["evolution_seed42"] = {
        "split": sp, "accepted": [{"edit": str(e), "origin": e.origin, "failures": [b, a]} for e, b, a in rep.rounds],
        "rejected": {k: {"origin": e.origin, "reason": why} for k, (e, why) in sorted(rep.rejected.items())},
        "test": evo}
    md += ["## Offline ontology evolution (40/10/50 split by task, seed 42, support from 2 task groups)", ""]
    for e, b, a in rep.rounds:
        md.append(f"- accepted `{e}` ({e.origin}): training failures {b} -> {a}")
    for k, (e, why) in sorted(rep.rejected.items()):
        md.append(f"- rejected `{k}` ({e.origin}): {why}")
    md += ["", f"Held-out test split ({len(test)} tasks):", "", table(evo), ""]

    # 5. how much training coverage the evolved TBox needs
    ref = sorted(str(a) for a in reference_tbox() if a.rel == "requires")
    sweep = []
    for ms in (1, 2):
        for frac in FRACTIONS:
            asr, au, n, hits = [], [], [], {a: 0 for a in ref}
            for seed in SEEDS:
                sp = split(seed, (frac, 0.1))
                tb = evolve(sp["train"], sp["val"], min_support=ms).tbox
                s = score(episodes(gard(tb, name="e"), "e", tasks=set(sp["test"])))
                asr.append(s["asr_actions"])
                au.append(s["au"])
                learned = [str(a) for a in tb if a.rel == "requires"]
                n.append(len(learned))
                for a in learned:
                    hits[a] += 1
            sweep.append({"min_support": ms, "train_fraction": frac, "action_asr_mean": r3(statistics.mean(asr)),
                          "action_asr_max": r3(max(asr)), "zero_asr_splits": sum(x == 0 for x in asr),
                          "au_mean": r3(statistics.mean(au)), "axioms_mean": r3(statistics.mean(n)),
                          "learned": hits})
    data["coverage_sweep"] = sweep
    md += ["## How much training coverage evolution needs", "",
           f"Evolved TBox scored on the held-out tasks of {len(SEEDS)} random task splits per row (10% validation). "
           f"The reference TBox has {len(ref)} obligations.", "",
           "| support groups | train fraction | action ASR (mean) | action ASR (worst split) | splits at 0% | "
           "AU (mean) | obligations learned |", "|---:|---:|---:|---:|---:|---:|---:|"]
    for row in sweep:
        md.append(f"| {row['min_support']} | {row['train_fraction']:.1f} | {row['action_asr_mean']:.3f} | "
                  f"{row['action_asr_max']:.3f} | {row['zero_asr_splits']}/{len(SEEDS)} | {row['au_mean']:.3f} | "
                  f"{row['axioms_mean']:.1f} / {len(ref)} |")
    md += ["", "How often each reference obligation was learned (support from 2 groups, out of "
           f"{len(SEEDS)} splits):", "", "| obligation | " + " | ".join(f"train {f:.1f}" for f in FRACTIONS) + " |",
           "|---|" + "---:|" * len(FRACTIONS)]
    for a in ref:
        md.append(f"| `{a}` | " + " | ".join(str(r["learned"][a]) for r in sweep if r["min_support"] == 2) + " |")
    md.append("")
    return json.dumps(data, indent=1, sort_keys=True) + "\n", "\n".join(md)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    js, md = build()
    targets = {ROOT / "results" / "results.json": js, ROOT / "results" / "table.md": md}
    if args.check:
        stale = [p.name for p, text in targets.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
        if stale:
            sys.exit(f"out of date: {', '.join(stale)} - run python scripts/results.py")
        print("results are up to date")
    else:
        for p, text in targets.items():
            p.write_text(text, encoding="utf-8", newline="\n")
        print(md)
