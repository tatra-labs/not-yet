"""The ontology: a frozen TBox of action-state relations, and the per-episode ABox of verified facts.

The vocabulary is deliberately small and fixed. Tools never appear here, only the action classes they ground
to, so two banking APIs that both move money inherit the same constraints.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Action classes. Read-only classes may observe states; state-changing classes are what the gate adjudicates.
READ_ONLY = frozenset({"LookupDirectory", "ListRecords", "SearchCatalog", "ReadDocument", "ReadFreeText"})
STATE_CHANGING = frozenset({"Pay", "SchedulePayment", "ModifySchedule", "Transmit", "Share", "Delete", "Book",
                            "CreateEvent", "SetCredential"})
ACTIONS = READ_ONLY | STATE_CHANGING
# Free text (email bodies, reviews, memo lines) is where injections live. It may never verify anything.
FREE_TEXT = frozenset({"ReadFreeText"})

# State classes and the parameter role each one is about.
STATES = {"VerifiedPayee": "payee", "VerifiedAmount": "amount", "VerifiedRecipient": "recipient",
          "VerifiedSelection": "item"}
RELATIONS = ("requires", "establishes", "invalidates", "observes")


@dataclass(frozen=True, order=True)
class Axiom:
    rel: str
    action: str
    state: str

    def __str__(self):
        return f"{self.rel}({self.action}, {self.state})"


# The one fixed axiom of the seed TBox: a state-changing action must be grounded in the user's goal.
BOUNDARY = Axiom("boundary", "*", "GroundedInGoal")


@dataclass(frozen=True)
class TBox:
    axioms: frozenset
    version: str = "seed"

    def requires(self, action):
        return sorted(a.state for a in self.axioms if a.rel == "requires" and a.action == action)

    def observers(self, state):
        return sorted(a.action for a in self.axioms if a.rel == "observes" and a.state == state)

    def observes(self, action, state):
        return Axiom("observes", action, state) in self.axioms

    def edit(self, add=(), remove=(), version=None):
        return TBox(frozenset((self.axioms - set(remove)) | set(add)), version or self.version + "+")

    def __iter__(self):
        return iter(sorted(self.axioms))

    def __len__(self):
        return len(self.axioms)


def seed_tbox():
    """Boundary axiom plus which structured observers can verify which states. No obligations yet."""
    obs = [("LookupDirectory", "VerifiedPayee"), ("LookupDirectory", "VerifiedRecipient"),
           ("ListRecords", "VerifiedPayee"), ("ListRecords", "VerifiedRecipient"), ("ListRecords", "VerifiedAmount"),
           ("ReadDocument", "VerifiedPayee"), ("ReadDocument", "VerifiedAmount"),
           ("SearchCatalog", "VerifiedSelection")]
    return TBox(frozenset({BOUNDARY} | {Axiom("observes", a, s) for a, s in obs}), "seed")


def reference_tbox():
    """What a careful engineer would write by hand: every verifiable role of every state-changing action."""
    req = [("Pay", "VerifiedPayee"), ("Pay", "VerifiedAmount"), ("SchedulePayment", "VerifiedPayee"),
           ("SchedulePayment", "VerifiedAmount"), ("ModifySchedule", "VerifiedPayee"),
           ("ModifySchedule", "VerifiedAmount"), ("Transmit", "VerifiedRecipient"), ("Share", "VerifiedRecipient"),
           ("CreateEvent", "VerifiedRecipient"), ("Book", "VerifiedSelection")]
    return seed_tbox().edit(add={Axiom("requires", a, s) for a, s in req}, version="reference")


# ---------------------------------------------------------------- protected entailments and consistency

def protected_violations(tbox):
    """Properties no ontology edit may break. Returned as human-readable reasons; empty means preserved."""
    out = []
    if BOUNDARY not in tbox.axioms:
        out.append("removes the boundary axiom (state-changing actions must be grounded in the user goal)")
    for a in tbox:
        if a.rel in ("observes", "establishes") and a.state in STATES:
            if a.action in STATE_CHANGING:
                out.append(f"{a}: a state-changing action would certify its own precondition")
            elif a.action in FREE_TEXT:
                out.append(f"{a}: free text, where injections live, would count as verification")
    return out


def inconsistencies(tbox):
    """A small, exact reasoner for this vocabulary (the paper uses HermiT over OWL 2)."""
    out = []
    for a in tbox:
        if a.rel not in RELATIONS + ("boundary",):
            out.append(f"{a}: unknown relation")
        elif a.rel != "boundary" and (a.action not in ACTIONS or a.state not in STATES):
            out.append(f"{a}: outside the fixed vocabulary")
        elif a.rel == "requires":
            if a.action in READ_ONLY:
                out.append(f"{a}: read-only actions carry no obligations")
            if not any(o in READ_ONLY for o in tbox.observers(a.state)):
                out.append(f"{a}: nothing can ever observe {a.state}, so the action would wait forever")
        elif a.rel == "establishes" and Axiom("invalidates", a.action, a.state) in tbox.axioms:
            out.append(f"{a}: also invalidates the same state")
    return out


# ---------------------------------------------------------------- the episode ABox

@dataclass(frozen=True)
class Fact:
    state: str
    key: tuple
    value: object
    source: str = ""

    def __str__(self):
        return f"{self.state}{self.key!r} = {self.value!r}  [from {self.source}]"


@dataclass
class ABox:
    facts: list = field(default_factory=list)

    def add(self, fact):
        if fact not in self.facts:
            self.facts.append(fact)

    def values(self, state, key):
        return [f.value for f in self.facts if f.state == state and f.key == key]
