"""Learn which fields of tool outputs an agent actually uses, and trim the rest."""
from .projection import Projection, learn, project
from .traces import Event, Trace, load
from .usage import analyse

__all__ = ["Event", "Projection", "Trace", "analyse", "learn", "load", "project"]
__version__ = "0.0.1"
