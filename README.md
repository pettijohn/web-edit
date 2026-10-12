# web-edit

[![PyPI](https://img.shields.io/pypi/v/web-edit.svg)](https://pypi.org/project/web-edit/)
[![Changelog](https://img.shields.io/github/v/release/simonw/web-edit?include_prereleases&label=changelog)](https://github.com/simonw/web-edit/releases)
[![Tests](https://github.com/simonw/web-edit/actions/workflows/test.yml/badge.svg)](https://github.com/simonw/web-edit/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/web-edit/blob/main/LICENSE)

A web app for browsing, creating, editing, uploading, and downloading files in a folder. Built with Starlette and a bundled CodeMirror 6 editor, with a responsive interface for phones and desktops.

## Installation

Install this tool using `pip`:

```bash
pip install web-edit
```

Or `uv`:

```bash
uv tool install web-edit
```

Or run directly with `uvx`:

```bash
uvx web-edit --help
```

## Usage

Run `web-edit` in a folder to start a web app for that folder, or pass a directory:

```bash
web-edit ~/sites/mysite
```

The server defaults to `127.0.0.1:9000`. Use `-p/--port` to change the port and `-h/--host` to change the listening address. For access over Tailscale, bind to the machine's Tailscale IP:

```bash
web-edit ~/sites/mysite --host 100.x.y.z --port 9000
```

Open `http://100.x.y.z:9000` from a device on your tailnet. There is no authentication; this version is intended for trusted local or Tailscale access. `--host 0.0.0.0` listens on all IPv4 interfaces.

- Browse folders, including dotfiles, using breadcrumbs. Images smaller than 2 MB (2,000,000 bytes) display inline and open in a larger preview.
- Select a UTF-8 text file to edit with CodeMirror 6. Python, JavaScript/TypeScript, HTML, CSS, JSON, and Markdown have syntax highlighting. Other text formats use plain text mode.
- Use **Save** or **Cmd/Ctrl+S**. The app warns before leaving unsaved edits and refuses to overwrite a file that changed on disk since it was opened.
- Use **New file** and enter a path such as `notes/hello.txt`. Missing parent folders are created automatically.
- Choose **Upload files**, select or drop multiple files, and edit each destination path and filename before uploading. The destination folder field sets the default for files added afterward. Up to three files upload concurrently, each with its own progress bar, cancel action, and retry. Existing files require an explicit **Replace existing** confirmation.
- Download any regular file. Binary files and text files larger than 10 MiB are available for download instead of editing.

Saves use a temporary file and an atomic replacement. Existing file permissions, UTF-8 byte-order marks, and line endings are preserved; unchanged lines keep their original endings, and new or changed lines use the original file's most common ending. Symbolic links and special files are listed but not followed. All paths are relative to the selected root folder.

CodeMirror, application JavaScript, styles, and third-party license notices are included in the Python package. Running the installed app needs neither Node.js nor a CDN connection. File uploads have no application-level size limit and are spooled to disk as needed. Uploads are not resumable; retries send the file again.

## Development

Install the Python dependencies and run the app:

```bash
uv sync --extra test
uv run web-edit .
```

Run the tests:

```bash
uv run pytest
```

The frontend source is `frontend/app.js`; CSS and HTML live in `web_edit/static/`. To rebuild the checked-in JavaScript bundle after changing the frontend:

```bash
npm ci
npm run build
```

Commit `package-lock.json` and the regenerated files in `web_edit/static/` with frontend changes. The build also collects licenses for the bundled dependencies. Python package builds use these checked-in assets and do not run npm.

## Building and checking the wheel

Build the source distribution and the wheel, then install the wheel in a clean temporary environment and exercise the installed app:

```bash
uv build
uv run scripts/check_wheel.py dist/web_edit-0.1-py3-none-any.whl
```

The wheel check runs away from the checkout, verifies that CodeMirror and the other assets are inside the wheel, and checks static serving, CLI loading, file creation, repeated editing, downloads, and uploads using the installed package. CI also rebuilds the frontend and checks that the committed bundle is current.

The Starlette app can also be embedded with `from web_edit.app import create_app; app = create_app("/path/to/folder")`. JSON writes and multipart uploads require the `X-Web-Edit: 1` header; the browser sends it automatically to prevent cross-origin form writes.
