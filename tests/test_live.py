"""Talks to the real account; runs only with `pytest -m live` and a logged-in session."""

import pytest

from tgmcp import config
from tgmcp.telegram import factory

pytestmark = pytest.mark.live


def _settings() -> config.Settings:
    if not (config.home() / config.SECRETS_FILE).exists():
        pytest.skip("no secrets file: run `tgmcp login` first")
    try:
        return config.load_settings()
    except config.ConfigError as exc:
        pytest.skip(str(exc))


async def test_me_and_dialogs():
    gw = factory(_settings())
    try:
        me = await gw.me()
        assert me.name
        assert await gw.dialogs()
    finally:
        await gw.close()
