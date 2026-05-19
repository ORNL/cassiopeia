# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared pytest fixtures for Cassiopeia tests."""

import os
import pytest


@pytest.fixture(autouse=True)
def _llm_env_vars(monkeypatch):
    """Set required LLM env vars so tests don't need a .env file."""
    monkeypatch.setenv("LLM_SCORING_MODEL", "mock/model")
    monkeypatch.setenv("LLM_CHAT_MODEL", "mock/model")
