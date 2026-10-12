"""Exercise host validation through the actual app, including file operations."""

import asyncio

import pytest
from starlette.testclient import TestClient

from web_edit.app import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(
            tmp_path, host="100.1.2.3", allowed_hosts=["my-machine.example.ts.net"]
        ),
        base_url="http://100.1.2.3:9000",
    ) as client:
        yield client


@pytest.mark.parametrize(
    "host",
    [
        "100.1.2.3",
        "100.1.2.3:9000",
        "localhost",
        "LOCALHOST.:9000",
        "127.0.0.1:9000",
        "[::1]:9000",
        "[0:0:0:0:0:0:0:1]",
        "my-machine.example.ts.net",
        "MY-MACHINE.EXAMPLE.TS.NET.:9000",
    ],
)
def test_expected_hosts_can_read_and_write(client, tmp_path, host):
    headers = {"Host": host, "X-Web-Edit": "1"}
    assert client.get("/api/browse", headers=headers).status_code == 200
    response = client.put(
        "/api/text", headers=headers, json={"path": "created.txt", "content": "allowed"}
    )
    assert response.status_code == 200
    assert (tmp_path / "created.txt").read_text() == "allowed"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/static/app.js",
        "/api/browse",
        "/api/text?path=secret.txt",
        "/api/download?path=secret.txt",
        "/api/image?path=image.svg",
    ],
)
def test_rebinding_host_cannot_read_any_route(client, tmp_path, path):
    (tmp_path / "secret.txt").write_text("private contents")
    (tmp_path / "image.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    response = client.get(path, headers={"Host": "evil.example:9000"})
    assert response.status_code == 400
    assert response.json()["error"].startswith("Host not allowed.")
    assert "private contents" not in response.text
    assert "location" not in response.headers
    assert response.headers["cache-control"] == "no-store"


def test_rebinding_host_cannot_mutate_files_even_with_versions(client, tmp_path):
    (tmp_path / "keep.txt").write_text("keep")
    version = client.get("/api/text?path=keep.txt").json()["version"]
    entry = client.get("/api/browse").json()["entries"][0]
    # Both headers can contain the attacker's hostname in a rebinding attack.
    headers = {
        "Host": "evil.example:9000",
        "Origin": "http://evil.example:9000",
        "X-Web-Edit": "1",
        "X-Forwarded-Host": "100.1.2.3:9000",
        "Forwarded": "host=100.1.2.3:9000",
    }
    assert (
        client.put(
            "/api/text",
            headers=headers,
            json={"path": "keep.txt", "content": "replaced", "version": version},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/upload",
            headers=headers,
            data={"path": "injected.txt"},
            files={"file": ("file.txt", b"injected")},
        ).status_code
        == 400
    )
    assert (
        client.delete(
            "/api/file",
            headers=headers,
            params={
                "path": "keep.txt",
                "kind": "file",
                "version": entry["delete_version"],
            },
        ).status_code
        == 400
    )
    assert (tmp_path / "keep.txt").read_text() == "keep"
    assert not (tmp_path / "injected.txt").exists()


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "localhost.evil.example",
        "evil.localhost",
        "100.1.2.3.evil.example",
        "other.example.ts.net",
        "sub.my-machine.example.ts.net",
        "0.0.0.0",
        "[::]",
        "testserver",
        "",
        "localhost,evil.example",
        "localhost@evil.example",
        "localhost/path",
        "http://localhost",
        "localhost:bad",
        "localhost:9000:80",
        "localhost:65536",
        "localhost:0",
        " localhost",
        "localhost\t",
        "localhost\n",
        "localhost..",
        "[localhost]",
        "[::1",
        "::1",
        "[::1]:9000.evil.example",
    ],
)
def test_unknown_and_malformed_hosts_rejected(client, host):
    response = client.get("/api/browse", headers={"Host": host})
    assert response.status_code == 400


@pytest.mark.parametrize(
    "values",
    [
        [],
        ["localhost", "evil.example"],
        ["evil.example", "localhost"],
        ["localhost", "localhost"],
    ],
)
def test_missing_and_duplicate_host_headers_rejected(tmp_path, values):
    # TestClient supplies a missing Host automatically, so use raw ASGI headers.
    app = create_app(tmp_path)
    messages = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/browse",
                "headers": [(b"host", value.encode()) for value in values],
            },
            receive,
            send,
        )
    )
    assert messages[0]["status"] == 400


@pytest.mark.parametrize("bind", ["0.0.0.0", "::"])
def test_all_interface_bind_requires_explicit_remote_hosts(tmp_path, bind):
    with TestClient(
        create_app(tmp_path, host=bind), base_url="http://100.1.2.3"
    ) as client:
        assert client.get("/api/browse").status_code == 400
        assert (
            client.get("/api/browse", headers={"Host": "localhost"}).status_code == 200
        )
    with TestClient(
        create_app(tmp_path, host=bind, allowed_hosts=["100.1.2.3", "my-machine"]),
        base_url="http://100.1.2.3",
    ) as client:
        assert client.get("/api/browse").status_code == 200
        assert (
            client.get("/api/browse", headers={"Host": "my-machine:9000"}).status_code
            == 200
        )
        assert (
            client.get("/api/browse", headers={"Host": "evil.example"}).status_code
            == 400
        )


@pytest.mark.parametrize(
    "bind, extra, authority",
    [
        ("fd7a:115c:a1e0::12", [], "[fd7a:115c:a1e0:0:0:0:0:12]:9000"),
        ("my-machine", [], "MY-MACHINE.:9000"),
        ("127.0.0.1", ["CAFÉ.example."], "xn--caf-dma.example:9000"),
    ],
)
def test_host_normalization(tmp_path, bind, extra, authority):
    with TestClient(create_app(tmp_path, host=bind, allowed_hosts=extra)) as client:
        assert client.get("/api/browse", headers={"Host": authority}).status_code == 200


@pytest.mark.parametrize(
    "extra",
    [
        "*",
        "*.example.com",
        "http://example.com",
        "example.com:9000",
        "example.com/path",
        "",
    ],
)
def test_factory_rejects_invalid_host_configuration(tmp_path, extra):
    with pytest.raises(ValueError, match="exact hostname"):
        create_app(tmp_path, allowed_hosts=[extra])


def test_default_factory_allows_only_local_hosts(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
        assert client.get("/api/browse").status_code == 200
        assert (
            client.get("/api/browse", headers={"Host": "evil.example"}).status_code
            == 400
        )
        assert (
            client.get("/api/browse", headers={"Host": "100.1.2.3"}).status_code == 400
        )


def test_allowed_host_still_needs_write_header(client, tmp_path):
    response = client.put(
        "/api/text", json={"path": "blocked.txt", "content": "blocked"}
    )
    assert response.status_code == 403
    assert not (tmp_path / "blocked.txt").exists()
