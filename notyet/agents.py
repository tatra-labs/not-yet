"""Scripted reference agents and the context their plans run in.

A plan is a short Python function per user task that calls tools through ``ctx``. Two knobs change how it
behaves, and nothing else does:

* ``verify``: whether the agent looks identifiers up before using them (diligent) or takes the first one it
  read in an email, a document or a review (hasty). Hasty is not wrong in a clean environment; it just
  acts on unverified values.
* ``gullible``: whether it obeys instructions injected into tool outputs. Every attacked run uses a gullible
  agent, the worst case, so attack success is decided by the defense alone.

When a defense answers *waiting*, a recovering agent makes the read-only call the hint names and asks again.
When the gate reports a conflict it adopts the verified value from the task state. Neither applies to calls
the injection asked for: a refused injected call is dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .gate import FORBIDDEN, READY, WAITING


@dataclass(frozen=True)
class Profile:
    name: str
    verify: bool
    gullible: bool = True
    recover: bool = True


DILIGENT = Profile("diligent", verify=True)
HASTY = Profile("hasty", verify=False)
PROFILES = {p.name: p for p in (DILIGENT, HASTY)}


class RoundLimit(Exception):
    pass


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


@dataclass
class Step:
    tool: str
    args: dict
    origin: str          # plan | injection | recovery
    verdict: str
    reason: str
    executed: bool
    satisfied: frozenset = frozenset()


@dataclass
class Ctx:
    env: object
    defense: object
    profile: Profile
    injection: object = None      # InjectionTask or None
    text: str | None = None       # the rendered injection text
    cap: int = 15
    trace: list = field(default_factory=list)
    overrides: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)
    answer_parts: list = field(default_factory=list)   # what an injection asked the agent to tell the user
    said: list = field(default_factory=list)
    seen_injection: bool = False

    # -- tool calls
    def call(self, tool, origin="plan", **args):
        retries = 0
        while True:
            if len(self.trace) >= self.cap:
                raise RoundLimit
            d = self.defense.decide(tool, args)
            ok = d.verdict == READY
            result = self.env.call(tool, args) if ok else None
            self.trace.append(Step(tool, dict(args), origin, d.verdict, d.reason, ok, frozenset(d.satisfied)))
            if ok:
                self.defense.executed(tool, args, result, d)
                self._scan(result)
                return result
            if origin == "injection" or not self.profile.recover or retries >= 2:
                return None
            retries += 1
            if d.verdict == WAITING and d.hints:
                for hint_tool, hint_args in d.hints:
                    self.call(hint_tool, origin="recovery", **hint_args)
            elif d.verdict == FORBIDDEN and d.fix:
                args = {**args, **d.fix}
            else:
                return None

    def _scan(self, result):
        if self.text is None or self.seen_injection or not any(self.text in s for s in _strings(result)):
            return
        self.seen_injection = True
        if not self.profile.gullible:
            return
        inj = self.injection
        if inj.override:
            self.overrides[inj.override[0]] = inj.override[1]
        if inj.add:
            self.extras.setdefault(inj.add[0], []).append(inj.add[1])
        if inj.answer:
            self.answer_parts.append(inj.answer)
        if inj.act:
            inj.act(self)

    # -- how plans obtain values
    def resolve(self, role, seen, verify=None, kind=None):
        """``seen``: the value the agent already read somewhere. ``verify``: (tool, args, pick) to look it up.
        ``kind`` selects from an override that depends on what is being chosen (hotel, restaurant, car)."""
        value = seen
        if verify is not None and self.profile.verify:
            tool, args, pick = verify
            r = self.call(tool, **args)
            if r is not None:
                value = pick(r)
        o = self.overrides.get(role, value)
        return o.get(kind, value) if isinstance(o, dict) else o

    def recipients(self, role, values):
        return list(values) + self.extras.get(role, [])

    def say(self, text):
        self.said.append(text)

    @property
    def answer(self):
        return "\n".join(self.said + self.answer_parts)
