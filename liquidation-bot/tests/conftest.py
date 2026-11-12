import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from liqbot.sim import KEY, build_market  # noqa: E402


@pytest.fixture
def market(tmp_path, monkeypatch):
    monkeypatch.setenv("LIQBOT_PRIVATE_KEY", KEY)
    return build_market(str(tmp_path / "test.db"))
