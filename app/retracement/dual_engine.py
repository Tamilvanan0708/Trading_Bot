"""
Backward-compatibility shim.

The canonical Fib Retracement strategy engine has been renamed to
`app.retracement.fib_retracement_engine`.

All classes, functions, and symbols from `fib_retracement_engine` are exported
here so existing imports continue to function without breaking changes.
"""

from app.retracement.fib_retracement_engine import *  # noqa: F401, F403
from app.retracement.fib_retracement_engine import (
    DualRetracementEngine,
    FibRetracementEngine,
)

__all__ = [
    "DualRetracementEngine",
    "FibRetracementEngine",
]
