# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for the plant phenotyping pack's tests."""

from pathlib import Path

import pytest

from domains import load_domain, set_domain

PACK = load_domain(Path(__file__).resolve().parents[1])


@pytest.fixture(autouse=True)
def _llm_env_vars(monkeypatch):
    monkeypatch.setenv("LLM_CHAT_MODEL", "mock/model")


@pytest.fixture(autouse=True)
def plant_pack():
    set_domain(PACK)
    yield PACK
    set_domain(None)
