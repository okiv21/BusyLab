"""The analysis engine.

Deterministic from end to end. Every number in every finding is computed here;
the LLM's only jobs are to narrate what this produces and to route questions
back to it (spec 2). Nothing in this package imports the web stack, so the
whole engine runs against a file on a laptop.
"""

from .dataset import SalesFrame, build
from .engine import AnalysisResult, analyse, analyse_file

__all__ = ["AnalysisResult", "SalesFrame", "analyse", "analyse_file", "build"]
