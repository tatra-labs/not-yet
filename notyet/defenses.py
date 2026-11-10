"""The defenses compared in the harness. Every one exposes ``decide(tool, args)`` and ``executed(...)``.

Baselines are simplified, deterministic stand-ins for the paper's (Tool Filter, CaMeL-style data flow), built
on the same oracle information the gate gets so that differences come from the policy, not from parsing.
"""
from __future__ import annotations

from .gate import FORBIDDEN, READY, Decision, Gate
from .ontology import reference_tbox, seed_tbox

FREE_TEXT_KEYS = {"body", "text", "content", "reviews", "subject", "description", "notes", "memo"}


class NoDefense:
    name = "none"

    def __init__(self, task, suite):
        self.tools = suite.tools

    def decide(self, tool, args):
        return Decision(READY, "no defense")

    def executed(self, tool, args, result, decision=None):
        pass


class ToolFilter(NoDefense):
    """Expose only the tools the user task needs (AgentDojo's tool filter, given the oracle tool set)."""
    name = "tool-filter"

    def __init__(self, task, suite):
        super().__init__(task, suite)
        self.allowed = set(task.tools_needed)

    def decide(self, tool, args):
        if tool in self.allowed:
            return Decision(READY, "tool is on the task's list")
        return Decision(FORBIDDEN, f"{tool} is not among the tools this task needs")


def _scalars(obj, skip_free=True):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not (skip_free and k in FREE_TEXT_KEYS):
                yield from _scalars(v, skip_free)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _scalars(v, skip_free)
    elif obj is not None:
        yield obj


def _text(v):
    return f"{v:g}" if isinstance(v, float) else str(v)


class Taint(NoDefense):
    """Strict data-flow policy: every role argument of a state-changing call must come from the user's prompt
    or a trusted structured field (directory, bank record, catalogue). Documents and free text are untrusted.
    No per-task policies and no user confirmation, so it is safe and blunt.
    """
    name = "taint"

    def __init__(self, task, suite):
        super().__init__(task, suite)
        self.prompt = task.prompt
        self.trusted = set()

    def decide(self, tool, args):
        spec = self.tools[tool]
        if not spec.state_changing:
            return Decision(READY, "read-only")
        for param, role in spec.roles.items():
            vals = args.get(param)
            for v in vals if isinstance(vals, (list, tuple)) else [vals]:
                if v is None:
                    continue
                if _text(v) not in self.prompt and _text(v) not in self.trusted:
                    return Decision(FORBIDDEN, f"{param}={v!r} is derived from untrusted data")
        return Decision(READY, "all role arguments are trusted")

    def executed(self, tool, args, result, decision=None):
        if self.tools[tool].trust in ("directory", "record", "catalog"):
            self.trusted |= {_text(v) for v in _scalars(result)}


def gard(tbox_fn, waiting=True, name=None):
    def make(task, suite):
        return Gate(tbox_fn() if callable(tbox_fn) else tbox_fn, task.intents, suite.tools, waiting=waiting,
                    name=name)
    make.label = name
    return make


def registry(evolved=None):
    """name -> factory(task, suite). ``evolved`` is a TBox learned by notyet.evolve (optional)."""
    reg = {"none": NoDefense, "tool-filter": ToolFilter, "taint": Taint,
           "gard-seed": gard(seed_tbox, name="gard-seed")}
    if evolved is not None:
        reg["gard-evolved-no-wait"] = gard(evolved, waiting=False, name="gard-evolved-no-wait")
        reg["gard-evolved"] = gard(evolved, name="gard-evolved")
    reg["gard-reference-no-wait"] = gard(reference_tbox, waiting=False, name="gard-reference-no-wait")
    reg["gard-reference"] = gard(reference_tbox, name="gard-reference")
    return reg
