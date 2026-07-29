"""Shared test fixtures."""

import os
import sys

import pytest

# Ensure the project root is on sys.path for all tests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """Prevent tests from reading real env vars."""
    monkeypatch.setenv("BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("FATSECRET_CLIENT_ID", "dummy-client-id")
    monkeypatch.setenv("FATSECRET_CLIENT_SECRET", "dummy-secret")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
