from pathlib import Path

import click
import uvicorn

from .app import create_app


@click.command()
@click.version_option()
@click.argument(
    "directory",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--host", "-h", default="127.0.0.1", show_default=True, help="Address to listen on."
)
@click.option(
    "--port", "-p", default=9000, type=click.IntRange(1, 65535), show_default=True
)
def cli(directory, host, port):
    "Browse and edit files in DIRECTORY (defaults to the current folder)."
    click.echo(f"Serving {directory.resolve()} at http://{host}:{port}")
    uvicorn.run(create_app(directory), host=host, port=port)
