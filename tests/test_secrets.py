import json
import sys
from pathlib import Path

import pytest

from tgmcp import config, secrets

HASH = "0123456789abcdef0123456789abcdef"
SESSION = "1AaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPp"
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


@pytest.fixture(autouse=True)
def _no_icacls(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    restricted: list[Path] = []
    monkeypatch.setattr(config, "restrict_to_owner", restricted.append)
    return restricted


@windows_only
def test_protect_round_trip_hides_the_data():
    data = b"the login key"
    blob = secrets.protect(data)
    assert data not in blob
    assert secrets.unprotect(blob) == data
    assert secrets.PLAIN is False


@windows_only
def test_tampered_blob_is_a_secrets_error():
    blob = bytearray(secrets.protect(b"the login key"))
    blob[-1] ^= 0xFF
    with pytest.raises(secrets.SecretsError, match="tgmcp login"):
        secrets.unprotect(bytes(blob))


def test_write_secrets_round_trip_without_clear_text(tmp_path: Path, _no_icacls: list[Path]):
    path = tmp_path / "secrets.bin"
    secrets.write_secrets(path, HASH, SESSION)
    raw = path.read_bytes()
    if not secrets.PLAIN:
        assert HASH.encode() not in raw
        assert SESSION.encode() not in raw
    assert secrets.read_secrets(path) == secrets.Secrets(api_hash=HASH, session=SESSION)
    assert _no_icacls == [path]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["secrets.bin"]


def test_read_missing_file_is_no_secrets(tmp_path: Path):
    with pytest.raises(secrets.NoSecrets):
        secrets.read_secrets(tmp_path / "secrets.bin")


def test_plain_platform_stores_the_payload_as_is(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(secrets, "PLAIN", True)
    path = tmp_path / "secrets.bin"
    secrets.write_secrets(path, HASH, SESSION)
    assert json.loads(path.read_bytes()) == {"v": 1, "api_hash": HASH, "session": SESSION}
    assert secrets.read_secrets(path).session == SESSION


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"\xff\xfe",
        b"[1]",
        json.dumps({"v": 2, "api_hash": HASH, "session": SESSION}).encode(),
        json.dumps({"v": 1, "api_hash": HASH}).encode(),
        json.dumps({"v": 1, "api_hash": 5, "session": SESSION}).encode(),
    ],
)
def test_unreadable_payload_is_a_secrets_error(tmp_path: Path, monkeypatch, payload: bytes):
    monkeypatch.setattr(secrets, "unprotect", lambda blob: payload)
    path = tmp_path / "secrets.bin"
    path.write_bytes(b"blob")
    with pytest.raises(secrets.SecretsError, match="tgmcp login"):
        secrets.read_secrets(path)


def test_foreign_blob_is_a_secrets_error(tmp_path: Path, monkeypatch):
    def refuse(blob: bytes) -> bytes:
        raise secrets.SecretsError(secrets.FOREIGN)

    monkeypatch.setattr(secrets, "unprotect", refuse)
    path = tmp_path / "secrets.bin"
    path.write_bytes(b"blob")
    with pytest.raises(secrets.SecretsError, match="cannot be decrypted"):
        secrets.read_secrets(path)
