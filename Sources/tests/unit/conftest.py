"""The native layer skips only where no compiler exists. Set NEMESIS_REQUIRE_NATIVE so it cannot."""
from __future__ import annotations

import os
import shutil

import pytest

import native_unit


@pytest.fixture(scope="session")
def _native_session():
    if not native_unit.compiler_available():
        if os.environ.get("NEMESIS_REQUIRE_NATIVE"):
            pytest.fail("NEMESIS_REQUIRE_NATIVE is set but gcc or zlib.h is missing")
        pytest.skip("needs Linux with gcc and zlib headers")
    unit = native_unit.session_unit()
    yield unit
    unit.kill_all()
    shutil.rmtree(unit.root, ignore_errors=True)
    if not os.environ.get("NEMESIS_NATIVE_BUILD"):
        shutil.rmtree(unit.binary.parent, ignore_errors=True)


@pytest.fixture
def nem(_native_session):
    _native_session.reset()
    yield _native_session
    _native_session.kill_all()
