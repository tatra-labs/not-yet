"""not-yet: ready / waiting / forbidden governance for tool-using agents (after SecureGARD, arXiv 2611.06682)."""
from .gate import FORBIDDEN, READY, WAITING, Criterion, Decision, Entity, Free, Gate, Intent, Literal, Source, ToolSpec
from .ontology import Axiom, TBox, reference_tbox, seed_tbox

__version__ = "0.1.0"
__all__ = ["Axiom", "Criterion", "Decision", "Entity", "FORBIDDEN", "Free", "Gate", "Intent", "Literal", "READY",
           "Source", "TBox", "ToolSpec", "WAITING", "reference_tbox", "seed_tbox"]
