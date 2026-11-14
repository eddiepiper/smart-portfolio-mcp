import json
from datetime import date, datetime
from pathlib import Path

import pytest

from merlin_quant.calendar import NY
from merlin_quant.config import EXAMPLE_CONFIG, Config, load_config
from merlin_quant.data import synthetic_fixture

CUTOFF = date(2026, 11, 6)                                  # a completed Friday session
NOW = datetime(2026, 11, 6, 21, 0, tzinfo=NY)               # after that close, before the next open


@pytest.fixture
def cfg() -> Config:
    return load_config()


@pytest.fixture
def md():
    return synthetic_fixture()


@pytest.fixture
def team(tmp_path, cfg, md):
    from merlin_quant.workflow import Team
    t = Team(tmp_path / "home", cfg, md, runtime_override="scripted")
    t.engine.init()
    t.engine.open()
    return t


def cfg_with(**risk) -> Config:
    data = json.loads(Path(EXAMPLE_CONFIG).read_text())
    data["risk"].update(risk)
    return Config.model_validate(data)
