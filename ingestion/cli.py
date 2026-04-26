"""Ingestion CLI."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import click

from ingestion import pipeline, watcher, worker
from shared.config import get_settings


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    """Contract intelligence ingestion CLI."""
    _setup_logging(verbose)


@cli.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def process(path: Path) -> None:
    """Process a single PDF synchronously (no queue)."""
    result = pipeline.process_file(path)
    click.echo(
        f"OK document={result.document_id} contract={result.contract_id} "
        f"rule={result.rule_id}/{result.rule_version} chunks={result.num_chunks}"
    )


@cli.command("worker")
def worker_cmd() -> None:
    """Drain pending jobs in a loop."""
    worker.run_forever()


@cli.command()
def watch() -> None:
    """Watch the folder and run a worker in the same process.

    Convenient for the demo. For production, run watcher and worker(s) separately.
    """
    settings = get_settings()
    t = threading.Thread(target=worker.run_forever, name="worker", daemon=True)
    t.start()
    watcher.run(settings.watch_folder)


@cli.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path),
                required=False)
def scan(folder: Path | None) -> None:
    """One-shot enqueue of every PDF under FOLDER (or WATCH_FOLDER)."""
    target = folder or get_settings().watch_folder
    n = watcher.scan_existing(target)
    click.echo(f"enqueued {n} files")


if __name__ == "__main__":
    cli()
