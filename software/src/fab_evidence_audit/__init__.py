"""Public interface for fixed-context evidence auditing."""
from .api import EvaluationLimit, InvalidTask, audit, check_witness, normalize

__version__ = "0.1.1"
__all__ = ["EvaluationLimit", "InvalidTask", "audit", "check_witness", "normalize", "__version__"]
