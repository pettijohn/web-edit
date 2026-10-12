"""Install a built wheel away from the checkout and exercise its packaged app.

Usage: uv run scripts/check_wheel.py dist/web_edit-0.1-py3-none-any.whl
"""

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

wheel = Path(sys.argv[1]).resolve()
required = {
    "web_edit/static/index.html",
    "web_edit/static/app.css",
    "web_edit/static/app.js",
    "web_edit/static/THIRD_PARTY_LICENSES.txt",
}
with zipfile.ZipFile(wheel) as archive:
    assert required <= set(archive.namelist()), "Missing frontend assets in wheel"
    assert b"cm-editor" in archive.read("web_edit/static/app.js"), (
        "CodeMirror missing from bundle"
    )
    assert b"@codemirror/view" in archive.read(
        "web_edit/static/THIRD_PARTY_LICENSES.txt"
    )

with tempfile.TemporaryDirectory(prefix="web-edit-wheel-") as temporary:
    directory = Path(temporary)
    environment = directory / ".venv"
    subprocess.run(["uv", "venv", str(environment)], check=True)
    python = environment / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), f"{wheel}[test]"], check=True
    )
    # -I removes both the checkout and PYTHONPATH from the import search path.
    subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--python",
            str(python),
            "python",
            "-I",
            "-c",
            r"""
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from starlette.testclient import TestClient
from click.testing import CliRunner
import web_edit
from web_edit.app import create_app
from web_edit.cli import cli

assert "site-packages" in web_edit.__file__, web_edit.__file__
assert files("web_edit").joinpath("static/app.js").is_file()
with TemporaryDirectory() as folder, TestClient(create_app(folder)) as client:
    assert client.get("/").status_code == 200
    assert "cm-editor" in client.get("/static/app.js").text
    assert client.get("/static/app.css").status_code == 200
    assert "@codemirror/view" in client.get("/static/THIRD_PARTY_LICENSES.txt").text
    created = client.put("/api/text", headers={"X-Web-Edit": "1"}, json={"path": "nested/test.txt", "content": "wheel works"})
    assert created.status_code == 200, created.text
    updated = client.put("/api/text", headers={"X-Web-Edit": "1"}, json={"path": "nested/test.txt", "content": "saved again", "version": created.json()["version"]})
    assert updated.status_code == 200, updated.text
    assert client.get("/api/download", params={"path": "nested/test.txt"}).content == b"saved again"
    uploaded = client.post("/api/upload", headers={"X-Web-Edit": "1"}, data={"path": "upload.bin"}, files={"file": ("original.bin", b"\x00\xff")})
    assert uploaded.status_code == 201, uploaded.text
    assert Path(folder, "upload.bin").read_bytes() == b"\x00\xff"
assert CliRunner().invoke(cli, ["--help"]).exit_code == 0
print("Wheel verified: isolated installation, bundled CodeMirror, assets, CLI, create/edit/download/upload.")
""",
        ],
        cwd=directory,
        check=True,
    )
