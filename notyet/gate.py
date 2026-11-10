"""Grounding tool calls into semantic actions, and the deterministic ready / waiting / forbidden decision."""
from __future__ import annotations

from dataclasses import dataclass, field

from .ontology import ABox, STATE_CHANGING, STATES, TBox

READY, WAITING, FORBIDDEN = "ready", "waiting", "forbidden"
RANK = {READY: 0, WAITING: 1, FORBIDDEN: 2}


# ---------------------------------------------------------------- what the user asked for

@dataclass(frozen=True)
class Literal:
    """The user gave the value itself ("send 50", "view access")."""
    value: object

    def __str__(self):
        return repr(self.value)


@dataclass(frozen=True)
class Entity:
    """The user named a party ("my landlord Westfield Properties"); its identifier must be looked up."""
    name: str

    @property
    def key(self):
        return ("entity", self.name)

    def __str__(self):
        return self.name


@dataclass(frozen=True)
class Source:
    """The user delegated the value to a record or document ("the amount on the bill")."""
    ref: str

    @property
    def key(self):
        return ("source", self.ref)

    def __str__(self):
        return f"<{self.ref}>"


@dataclass(frozen=True)
class Criterion:
    """The user described a choice ("the best-rated hotel in Paris")."""
    category: str
    city: str
    objective: str
    cuisine: str | None = None

    @property
    def key(self):
        return ("criterion", self.category, self.city, self.objective, self.cuisine)

    def __str__(self):
        return f"{self.objective} {self.cuisine + ' ' if self.cuisine else ''}{self.category} in {self.city}"


@dataclass(frozen=True)
class Free:
    """The user left it open (a subject line, a message body)."""

    def __str__(self):
        return "*"


@dataclass(frozen=True)
class Intent:
    """One state-changing action the user's request authorises, with what each role is bound to."""
    action: str
    bindings: tuple  # ((role, Binding | tuple[Binding, ...]), ...)
    count: int = 1

    @staticmethod
    def of(action, count=1, **bindings):
        return Intent(action, tuple(sorted(bindings.items())), count)

    @property
    def roles(self):
        return dict(self.bindings)

    def __str__(self):
        def show(b):
            return "[" + ", ".join(map(str, b)) + "]" if isinstance(b, tuple) else str(b)
        return f"{self.action}(" + ", ".join(f"{r}={show(b)}" for r, b in self.bindings) + ")"


# ---------------------------------------------------------------- what the tools are

@dataclass
class ToolSpec:
    name: str
    action: str                       # action class from the ontology vocabulary
    roles: dict = field(default_factory=dict)   # parameter -> semantic role; other params are free
    trust: str = "text"               # directory | record | catalog | document | text  (for the taint baseline)
    facts: object = None              # (args, result) -> list[Fact]   structured observations
    hint: object = None               # (binding) -> args dict, if this tool can observe that binding
    description: str = ""
    params: dict = field(default_factory=dict)  # name -> JSON-schema type, for LLM adapters

    @property
    def state_changing(self):
        return self.action in STATE_CHANGING


@dataclass
class Action:
    tool: str
    action: str
    roles: dict   # role -> value (a list for list-valued roles)
    args: dict

    def __str__(self):
        return f"{self.action}<{self.tool}>(" + ", ".join(f"{r}={v!r}" for r, v in self.roles.items()) + ")"


def ground(spec, args):
    roles = {}
    for param, role in spec.roles.items():
        if param in args and args[param] is not None:
            v = args[param]
            roles[role] = list(v) if isinstance(v, (list, tuple)) else v
    return Action(spec.name, spec.action, roles, dict(args))


@dataclass
class Decision:
    verdict: str
    reason: str
    missing: list = field(default_factory=list)   # [(state, binding)]
    hints: list = field(default_factory=list)     # [(tool, args)] read-only calls that would observe them
    fix: dict = field(default_factory=dict)       # param -> verified value, offered after a conflict
    intent: Intent | None = None
    satisfied: set = field(default_factory=set)   # verified states, whether or not the TBox requires them

    def __str__(self):
        return f"{self.verdict.upper()}: {self.reason}"


# ---------------------------------------------------------------- the gate

def _eq(a, b):
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return False
    return a == b


class Gate:
    """SecureGARD-style governance: frozen TBox + episode ABox + the user's intents.

    ``waiting=False`` turns every unmet obligation into a refusal (the no-waiting ablation).
    """

    def __init__(self, tbox: TBox, intents, tools, waiting=True, name=None):
        self.tbox, self.tools, self.waiting = tbox, tools, waiting
        self.remaining = {i: i.count for i in intents}
        self.abox = ABox()
        self.history = []
        self.name = name or ("gard" if waiting else "gard-no-wait")

    # -- public API, shared by every defense
    def decide(self, tool, args):
        spec = self.tools[tool]
        if not spec.state_changing:
            return Decision(READY, f"{spec.action} is read-only")
        act = ground(spec, args)
        admitting = [i for i, n in self.remaining.items() if n > 0 and i.action == act.action]
        if not admitting:
            return Decision(FORBIDDEN, f"{act.action} is not part of the user's request"
                            if not any(i.action == act.action for i in self.remaining)
                            else f"the user's request allows {act.action} only "
                                 f"{sum(i.count for i in self.remaining if i.action == act.action)} time(s)")
        best = None
        for intent in admitting:
            d = self._against(act, intent, spec)
            if best is None or RANK[d.verdict] < RANK[best.verdict]:
                best = d
        return best

    def executed(self, tool, args, result, decision=None):
        spec = self.tools[tool]
        if spec.facts and result is not None:
            for f in spec.facts(args, result):
                if self.tbox.observes(spec.action, f.state):
                    self.abox.add(f)
        if spec.state_changing and decision is not None and decision.intent is not None:
            self.remaining[decision.intent] -= 1
        self.history.append((tool, args))

    # -- internals
    def _against(self, act, intent, spec):
        roles = intent.roles
        for role, value in act.roles.items():
            if role not in roles:
                return Decision(FORBIDDEN, f"sets {role}={value!r}, which the user's request does not grant",
                                intent=intent)
            b = roles[role]
            if isinstance(b, tuple):
                vals = value if isinstance(value, list) else [value]
                if len(vals) != len(b):
                    return Decision(FORBIDDEN, f"{role} has {len(vals)} value(s); the request names {len(b)}",
                                    fix=self._trim(spec, role, vals, b), intent=intent)
            elif isinstance(b, Literal) and not _eq(value, b.value):
                param = next(p for p, r in spec.roles.items() if r == role)
                return Decision(FORBIDDEN, f"{role}={value!r} but the user said {b.value!r}",
                                fix={param: b.value}, intent=intent)
        status = self._verify(act, roles, spec)
        required = set(self.tbox.requires(act.action))
        satisfied = {s for s, (ok, _, _) in status.items() if ok}
        conflicts = [msg for s, (ok, msg, _) in status.items() if s in required and msg and not ok and msg[0] == "!"]
        missing = [m for s, (ok, msg, miss) in status.items() if s in required and not ok and not conflicts
                   for m in miss]
        if conflicts:
            fix = {}
            for s in required & set(status):
                if isinstance(status[s][2], dict):
                    fix.update(status[s][2])
            return Decision(FORBIDDEN, "; ".join(c[1:] for c in conflicts), fix=fix, intent=intent,
                            satisfied=satisfied)
        if missing:
            hints = self._hints(missing)
            text = "; ".join(f"{s} for {b}" for s, b in missing)
            if not self.waiting:
                return Decision(FORBIDDEN, f"unmet obligation: {text}", missing=missing, intent=intent,
                                satisfied=satisfied)
            how = ", ".join(f"{t}({', '.join(f'{k}={v!r}' for k, v in a.items())})" for t, a in hints)
            return Decision(WAITING, f"needs {text}" + (f"; observe it with {how}" if how else ""),
                            missing=missing, hints=hints, intent=intent, satisfied=satisfied)
        return Decision(READY, f"grounded in {intent}", intent=intent, satisfied=satisfied)

    def _verify(self, act, roles, spec):
        """For every verifiable role: (verified?, conflict message or '', missing list or fix dict)."""
        out = {}
        for state, role in STATES.items():
            if role not in act.roles or role not in roles:
                continue
            listy = isinstance(roles[role], tuple)
            binds = roles[role] if listy else (roles[role],)
            vals = act.roles[role] if isinstance(act.roles[role], list) else [act.roles[role]]
            unused, missing, conflict, verified = list(vals), [], [], []
            for b in binds:
                if isinstance(b, (Literal, Free)):
                    continue
                known = self.abox.values(state, b.key)
                hit = next((v for v in unused if any(_eq(v, k) for k in known)), None)
                if hit is not None:
                    unused.remove(hit)
                    verified.append(hit)
                elif known:
                    conflict.append(f"{role} for {b} is verified as {known[0]!r}, not "
                                    f"{', '.join(map(repr, unused)) or 'that'}")
                    verified.append(known[0])
                else:
                    missing.append((state, b))
            if not any(isinstance(b, (Entity, Source, Criterion)) for b in binds):
                continue
            param = next(p for p, r in spec.roles.items() if r == role)
            if conflict:
                fix = {param: verified if listy else verified[0]} if not missing else {}
                out[state] = (False, "!" + "; ".join(conflict), fix)
            elif missing:
                out[state] = (False, "", missing)
            else:
                out[state] = (True, "", [])
        return out

    def _trim(self, spec, role, vals, binds):
        """After an over-long list: offer the values that are verified for each binding, if all of them are."""
        state = next((s for s, r in STATES.items() if r == role), None)
        keep = []
        for b in binds:
            known = self.abox.values(state, b.key) if hasattr(b, "key") else []
            hit = next((v for v in vals if v not in keep and any(_eq(v, k) for k in known)), None)
            if hit is None:
                return {}
            keep.append(hit)
        return {next(p for p, r in spec.roles.items() if r == role): keep}

    def _hints(self, missing):
        out = []
        for state, binding in missing:
            for spec in self.tools.values():
                if spec.hint and spec.action in self.tbox.observers(state):
                    args = spec.hint(binding)
                    if args is not None and (spec.name, args) not in out:
                        out.append((spec.name, args))
        return out
