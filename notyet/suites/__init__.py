from .banking import SUITE as BANKING
from .travel import SUITE as TRAVEL
from .workspace import SUITE as WORKSPACE

SUITES = {s.name: s for s in (BANKING, WORKSPACE, TRAVEL)}
