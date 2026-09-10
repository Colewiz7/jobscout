import pathlib

import pytest

from jobscout.config import Config

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def config():
    return Config.load(ROOT / "config" / "filters.yaml")


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text()


@pytest.fixture
def fixture_json():
    import json

    return lambda name: json.loads((FIXTURES / name).read_text())
