"""Offline ontology evolution: propose axioms, admit only the ones that pass every check, freeze the result.

The acceptance procedure follows the paper. A candidate edit must be (1) supported by training evidence from at
least ``min_support`` independent task groups, (2) consistent, (3) preserve the protected entailments, (4) fix
a failure seen in training, and (5) survive validation, which can only veto (clean utility may not drop and
attack success may not rise). Among admissible edits the smallest wins (all edits here are single axioms, so
ties are broken by how many training failures each fixes). The loop stops when nothing is admissible.

Two proposers are included. ``extract`` is deterministic induction from evidence. ``scripted_llm`` stands in
for the paper's GPT-4o hypothesis generator: a fixed list of plausible proposals, some good and some that
would open holes, so that every rejection path is exercised. ``llm_proposer`` asks a real model through an
OpenAI-compatible endpoint; it is wired up but has not been run here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .defenses import gard
from .harness import episodes, score
from .ontology import BOUNDARY, STATE_CHANGING, STATES, Axiom, TBox, inconsistencies, protected_violations, seed_tbox
from .suites import SUITES


@dataclass(frozen=True)
class Edit:
    add: tuple = ()
    remove: tuple = ()
    origin: str = "extract"
    rationale: str = ""

    def __str__(self):
        return " ".join([f"+{a}" for a in self.add] + [f"-{a}" for a in self.remove])

    @property
    def size(self):
        return len(self.add) + len(self.remove)


@dataclass(frozen=True)
class Evidence:
    action: str
    satisfied: frozenset
    harmful: bool
    group: str


@dataclass
class Report:
    tbox: TBox
    rounds: list = field(default_factory=list)       # [(accepted Edit, failures before, failures after)]
    rejected: dict = field(default_factory=dict)     # str(edit) -> (Edit, reason)


def _injected_values(inj):
    vals = set()
    for pair in (inj.override, inj.add):
        if pair:
            v = pair[1]
            vals |= set(v.values()) if isinstance(v, dict) else {v}
    return vals


def _flat(args):
    for v in args.values():
        if isinstance(v, (list, tuple)):
            yield from v
        else:
            yield v


def collect(tbox, tasks):
    """Run the tasks under the gate with ``tbox``; return (evidence, failures, episodes)."""
    eps = episodes(gard(tbox, name="train"), "train", tasks=set(tasks))
    injections = {i.id: i for s in SUITES.values() for i in s.injection_tasks}
    specs = {t.id: s.tools for s in SUITES.values() for t in s.user_tasks}
    evidence, failures = [], []
    for e in eps:
        bad = _injected_values(injections[e.injection]) if e.injection else set()
        for s in e.trace:
            if s.executed and specs[e.task][s.tool].state_changing:
                harmful = s.origin == "injection" or any(v in bad for v in _flat(s.args) if isinstance(v, str)
                                                         or isinstance(v, float))
                evidence.append(Evidence(specs[e.task][s.tool].action, s.satisfied, harmful, e.task))
        if e.success or (e.injection is None and not e.utility):
            failures.append(e)
    return evidence, failures, eps


def supported(edit, evidence, min_support):
    """Only ``requires`` axioms can be induced from execution evidence; they need presence-enables support from
    ``min_support`` task groups and at least one harmful execution where the state was absent."""
    for a in edit.add:
        if a.rel != "requires":
            return f"{a}: execution evidence cannot induce a {a.rel} relation here"
        groups = {e.group for e in evidence if e.action == a.action and not e.harmful and a.state in e.satisfied}
        if len(groups) < min_support:
            return f"{a}: supported by {len(groups)} task group(s), needs {min_support}"
        if not any(e.action == a.action and e.harmful and a.state not in e.satisfied for e in evidence):
            return f"{a}: no harmful execution without {a.state}, so nothing shows it is needed"
    return ""


def extract(evidence, tbox):
    """Deterministic induction: every requires(a, s) with harmful-without-s and benign-with-s evidence."""
    out = []
    for a in sorted(STATE_CHANGING):
        for s in sorted(STATES):
            ax = Axiom("requires", a, s)
            if ax in tbox.axioms:
                continue
            harm = any(e.action == a and e.harmful and s not in e.satisfied for e in evidence)
            good = any(e.action == a and not e.harmful and s in e.satisfied for e in evidence)
            if harm and good:
                out.append(Edit((ax,), (), "extract", "harmful without it, benign with it"))
    return out


def scripted_llm(evidence, tbox):
    """A stand-in for an LLM hypothesis generator: plausible proposals, several of them unsafe."""
    A = Axiom
    pool = [
        Edit((A("requires", "Transmit", "VerifiedRecipient"),), (), "llm", "emails went to unverified addresses"),
        Edit((A("requires", "Book", "VerifiedSelection"),), (), "llm", "bookings did not match the user's criterion"),
        Edit((A("observes", "ReadFreeText", "VerifiedRecipient"),), (), "llm",
             "email bodies often contain the right address, so reading one should count as verification"),
        Edit((A("establishes", "Pay", "VerifiedPayee"),), (), "llm", "a payment that went through proves the payee"),
        Edit((), (BOUNDARY,), "llm", "the boundary blocked follow-up actions the injected notes asked for"),
        Edit((A("requires", "ReadDocument", "VerifiedAmount"),), (), "llm", "check amounts before reading bills"),
        Edit((A("requires", "Delete", "VerifiedRecipient"),), (), "llm", "deletions should be confirmed"),
        Edit((A("requires", "Pay", "VerifiedSelection"),), (), "llm", "payments should match the user's choice"),
        Edit((), (A("observes", "ListRecords", "VerifiedRecipient"),), "llm", "email headers can be spoofed"),
        Edit((A("requires", "Share", "VerifiedRecipient"),), (), "llm", "files were shared with the wrong people"),
    ]
    return [e for e in pool if not set(e.add) <= tbox.axioms or any(r in tbox.axioms for r in e.remove)]


def llm_proposer(base_url, model, api_key=""):  # pragma: no cover - needs a live endpoint
    """Ask a model for edits. It sees only the vocabulary and aggregated evidence, never trajectories."""
    import json
    import urllib.request

    def propose(evidence, tbox):
        summary = {}
        for e in evidence:
            k = f"{e.action} harmful={e.harmful} satisfied={sorted(e.satisfied)}"
            summary[k] = summary.get(k, 0) + 1
        prompt = ("You maintain an ontology for an agent firewall. Relations: requires, establishes, invalidates, "
                  f"observes. Action classes: {sorted(STATE_CHANGING)} plus read-only classes. States: "
                  f"{sorted(STATES)}.\nCurrent axioms: {[str(a) for a in tbox]}\nEvidence counts: {summary}\n"
                  'Reply with JSON only: [{"add": [["requires","Pay","VerifiedPayee"]], "remove": [], '
                  '"rationale": "..."}]')
        req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {api_key}"},
                                     data=json.dumps({"model": model, "messages": [
                                         {"role": "user", "content": prompt}]}).encode())
        with urllib.request.urlopen(req, timeout=120) as r:
            text = json.load(r)["choices"][0]["message"]["content"]
        out = []
        for item in json.loads(text[text.index("["):text.rindex("]") + 1]):
            out.append(Edit(tuple(Axiom(*a) for a in item.get("add", [])),
                            tuple(Axiom(*a) for a in item.get("remove", [])), "llm", item.get("rationale", "")))
        return out
    return propose


def _static_check(edit, new, evidence, min_support):
    if protected_violations(new):
        return "protected: " + "; ".join(protected_violations(new))
    if inconsistencies(new):
        return "inconsistent: " + "; ".join(inconsistencies(new))
    why = supported(edit, evidence, min_support)
    return "unsupported: " + why if why else ""


def _validation(tbox, tasks):
    return score(episodes(gard(tbox, name="val"), "val", tasks=set(tasks)))


def evolve(train, val, proposers=(extract, scripted_llm), min_support=2, start=None, max_rounds=20):
    tbox = start or seed_tbox()
    report = Report(tbox)
    for _ in range(max_rounds):
        evidence, failures, _ = collect(tbox, train)
        if not failures:
            break
        base_val = _validation(tbox, val)
        cands = {}
        for p in proposers:
            for e in p(evidence, tbox):
                cands.setdefault(str(e), e)
        admissible = []
        for key, edit in sorted(cands.items()):
            new = tbox.edit(edit.add, edit.remove, version=f"v{len(report.rounds) + 1}")
            reason = _static_check(edit, new, evidence, min_support)
            if not reason:
                after = len(collect(new, train)[1])
                if after >= len(failures):
                    reason = f"fixes no training failure ({len(failures)} before, {after} after)"
            if not reason and val:
                v = _validation(new, val)
                if v["cu"] < base_val["cu"] - 1e-9 or v["asr"] > base_val["asr"] + 1e-9:
                    reason = (f"vetoed by validation (CU {base_val['cu']:.3f}->{v['cu']:.3f}, "
                              f"ASR {base_val['asr']:.3f}->{v['asr']:.3f})")
            if reason:
                report.rejected[key] = (edit, reason)
            else:
                admissible.append((edit.size, after, key, edit, new))
        if not admissible:
            break
        size, after, key, edit, new = min(admissible)
        report.rejected.pop(key, None)
        report.rounds.append((edit, len(failures), after))
        tbox = new
    report.tbox = TBox(tbox.axioms, "evolved")
    return report
