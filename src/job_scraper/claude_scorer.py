"""
Backward-compatible shim for the old Claude scorer import path.
"""

from .gemini_scorer import GeminiScorer


class ClaudeScorer(GeminiScorer):
    """
    Legacy alias so older imports keep working after the Gemini migration.
    """
