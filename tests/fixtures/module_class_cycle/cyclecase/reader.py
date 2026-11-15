from . import support
from .support import Fault, Cell

observed = (Fault is support.Fault, Cell is support.Cell,
            hasattr(support, 'Later'), Cell(42).value)
