from click.testing import CliRunner

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


def test_default_server_options(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "web_edit.cli.uvicorn.run", lambda app, **kwargs: calls.append(kwargs)
    )
    assert CliRunner().invoke(cli).exit_code == 0
    assert calls == [{"host": "127.0.0.1", "port": 9000}]


def test_invalid_directory(tmp_path):
    assert CliRunner().invoke(cli, [str(tmp_path / "missing")]).exit_code == 2
