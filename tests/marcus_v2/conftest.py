import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def rt(tmp_path):
    from marcus_v2.core.llm import MockLLM
    from marcus_v2.core.runtime import build_runtime
    return build_runtime(tmp_path / "home", llm=MockLLM(cache_dir=tmp_path / "llm"), environ={})
