"""
conftest.py — shared pytest configuration.

Loads the project's .env file before any test module is imported so that
environment variables like GEMINI_API_KEY are available to all tests.
"""
import os
from pathlib import Path

# Walk up from tests/ to find the project root (where .env lives)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed — keys must be set in shell
