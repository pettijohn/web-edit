# web-edit

[![PyPI](https://img.shields.io/pypi/v/web-edit.svg)](https://pypi.org/project/web-edit/)
[![Changelog](https://img.shields.io/github/v/release/simonw/web-edit?include_prereleases&label=changelog)](https://github.com/simonw/web-edit/releases)
[![Tests](https://github.com/simonw/web-edit/actions/workflows/test.yml/badge.svg)](https://github.com/simonw/web-edit/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/web-edit/blob/master/LICENSE)

Web app for editing files in a folder

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

Run this tool in a folder to start a localhost web app serving a UI for creating and editing files in that folder.

Pass a path to a folder to target that folder instead:

```bash
uvx web-edit ~/sites/mysite
```
Defaults to listening on `127.0.0.1:9000`.

Use `-p/--port` to specify a different port and `-h/--host` for a different host IP.

## Development

To contribute to this tool, first checkout the code. Then create a new virtual environment:
```bash
cd web-edit
python -m venv venv
source venv/bin/activate
```
Now install the dependencies and test dependencies:
```bash
pip install -e '.[test]'
```
To run the tests:
```bash
python -m pytest
```
