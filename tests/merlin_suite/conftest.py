import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from merlin_fixtures import make_report  # noqa: E402


@pytest.fixture
def report():
    return make_report()
