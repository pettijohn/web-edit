from pathlib import Path

import click
import uvicorn

from .app import create_app
from .security import normalize_host


def validate_host(ctx, param, value):
    try:
        if param.multiple:
            return tuple(normalize_host(host) for host in value)
        return normalize_host(value)
    except ValueError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=param) from error


@click.command()
@click.version_option()
@click.argument(
    "directory",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--host",
    "-h",
    default="127.0.0.1",
    show_default=True,
    callback=validate_host,
    help="Address to listen on.",
)
@click.option(
    "--allowed-host",
    "-a",
    "allowed_hosts",
    multiple=True,
    callback=validate_host,
    metavar="HOSTNAME",
    help="Additional hostname or IP to accept. Repeat for multiple hosts. Required for remote access when listening on 0.0.0.0 or ::.",
)
@click.option(
    "--port", "-p", default=9000, type=click.IntRange(1, 65535), show_default=True
)
def cli(directory, host, port, allowed_hosts):
    "Browse and edit files in DIRECTORY (defaults to the current folder)."
    url_host = f"[{host}]" if ":" in host else host
    click.echo(f"Serving {directory.resolve()} at http://{url_host}:{port}")
    uvicorn.run(
        create_app(directory, host=host, allowed_hosts=allowed_hosts),
        host=host,
        port=port,
    )
