"""Shared pytest fixtures."""

import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def test_settings() -> Settings:
    """Settings that ignore any local .env file."""
    return Settings(_env_file=None, environment="testing")


@pytest.fixture
def app(test_settings: Settings) -> FastAPI:
    """A fresh application instance per test."""
    return create_app(test_settings)