"""
Tests for ClaudeScorer legacy alias.
"""
import pytest
from job_scraper.claude_scorer import ClaudeScorer
from job_scraper.gemini_scorer import GeminiScorer

def test_claude_scorer_inheritance():
    """Test that ClaudeScorer inherits from GeminiScorer."""
    assert issubclass(ClaudeScorer, GeminiScorer)

def test_claude_scorer_instance():
    """Test that ClaudeScorer instance is also a GeminiScorer instance."""
    scorer = ClaudeScorer(api_key="test_key")
    assert isinstance(scorer, GeminiScorer)
    assert isinstance(scorer, ClaudeScorer)

def test_claude_scorer_methods():
    """Test that ClaudeScorer has the expected methods from GeminiScorer."""
    scorer = ClaudeScorer(api_key="test_key")
    assert hasattr(scorer, "score_job")
    assert hasattr(scorer, "score_batch")
    assert hasattr(scorer, "tailor_resume")
    assert callable(scorer.score_job)
    assert callable(scorer.score_batch)
    assert callable(scorer.tailor_resume)
