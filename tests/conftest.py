# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared pytest fixtures for Cassiopeia core tests.

Core tests run against a test-only domain pack (``fixtures/materials_pack``)
so the generic pipeline is exercised without any real community's vocabulary.
Pack-specific tests live next to their pack under ``domains/<pack>/tests``.
"""

from pathlib import Path

import pytest

from domains import load_domain, set_domain

TEST_PACK_DIR = Path(__file__).parent / "fixtures" / "materials_pack"
TEST_PACK = load_domain(TEST_PACK_DIR)


@pytest.fixture(autouse=True)
def _llm_env_vars(monkeypatch):
    """Set optional LLM env vars so tests don't need a .env file."""
    monkeypatch.setenv("LLM_CHAT_MODEL", "mock/model")


@pytest.fixture(autouse=True)
def _test_domain_pack():
    set_domain(TEST_PACK)
    yield TEST_PACK
    set_domain(None)
