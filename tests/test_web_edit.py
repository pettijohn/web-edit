import pytest
from click.testing import CliRunner
from starlette.testclient import TestClient

from web_edit.cli import cli


def test_version():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith("cli, version ")


def test_server_options(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "web_edit.cli.uvicorn.run", lambda app, **kwargs: calls.append((app, kwargs))
    )
    result = CliRunner().invoke(cli, [str(tmp_path), "-h", "100.1.2.3", "-p", "9011"])
    assert result.exit_code == 0, result.output
    assert calls[0][0].state.store.root == tmp_path.resolve()
    assert calls[0][1] == {"host": "100.1.2.3", "port": 9011}
    with TestClient(calls[0][0], base_url="http://100.1.2.3:9011") as client:
        assert client.get("/api/browse").status_code == 200
        assert (
            client.get("/api/browse", headers={"Host": "evil.example:9011"}).status_code
            == 400
        )


def test_default_server_options(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "web_edit.cli.uvicorn.run", lambda app, **kwargs: calls.append(kwargs)
    )
    assert CliRunner().invoke(cli).exit_code == 0
    assert calls == [{"host": "127.0.0.1", "port": 9000}]


def test_invalid_directory(tmp_path):
    assert CliRunner().invoke(cli, [str(tmp_path / "missing")]).exit_code == 2


def test_repeated_allowed_host_options(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "web_edit.cli.uvicorn.run", lambda app, **kwargs: calls.append(app)
    )
    result = CliRunner().invoke(
        cli,
        [
            str(tmp_path),
            "--host",
            "0.0.0.0",
            "-a",
            "my-machine",
            "--allowed-host",
            "100.1.2.3",
        ],
    )
    assert result.exit_code == 0, result.output
    with TestClient(calls[0]) as client:
        for host in ["my-machine", "100.1.2.3", "localhost"]:
            assert client.get("/api/browse", headers={"Host": host}).status_code == 200
        assert (
            client.get("/api/browse", headers={"Host": "other-machine"}).status_code
            == 400
        )


@pytest.mark.parametrize(
    "flag, value",
    [
        ("-a", "*"),
        ("--allowed-host", "*.example.com"),
        ("-a", "http://my-machine:9000"),
        ("--host", "*"),
    ],
)
def test_invalid_host_options_do_not_start_server(monkeypatch, tmp_path, flag, value):
    calls = []
    monkeypatch.setattr(
        "web_edit.cli.uvicorn.run", lambda *args, **kwargs: calls.append(args)
    )
    result = CliRunner().invoke(cli, [str(tmp_path), flag, value])
    assert result.exit_code == 2
    assert "exact hostname" in result.output
    assert calls == []


def test_ipv6_listening_url(monkeypatch, tmp_path):
    monkeypatch.setattr("web_edit.cli.uvicorn.run", lambda *args, **kwargs: None)
    result = CliRunner().invoke(cli, [str(tmp_path), "--host", "::1"])
    assert result.exit_code == 0
    assert "http://[::1]:9000" in result.output
