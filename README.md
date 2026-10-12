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
## Usage

For help, run:
```bash
web-edit --help
```
You can also use:
```bash
python -m web_edit --help
```
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
