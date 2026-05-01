"""Ingestion CLI."""
from __future__ import annotations

import os
import threading
from pathlib import Path

import click

from ingestion import pipeline, watcher, worker
from shared.config import get_settings
from shared.logging import configure_logging


def _setup_logging(verbose: bool) -> None:
    if verbose:
        os.environ.setdefault("LOG_LEVEL", "DEBUG")
    configure_logging()


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


@cli.command()
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True,
              help="Where to write the snapshot JSON.")
@click.option("--rule", "rule_filter", default=None,
              help="Only snapshot contracts under this rule_id.")
def snapshot(output: Path, rule_filter: str | None) -> None:
    """Capture the current corpus's extraction state to a JSON file.

    Use before a rule / prompt / chunker change so you can diff afterwards.
    """
    from ingestion.snapshots import snapshot_corpus, write_snapshot

    snap = snapshot_corpus(rule_id=rule_filter)
    write_snapshot(snap, output)
    click.echo(
        f"snapshot: {len(snap['contracts'])} contracts written to {output}"
    )


@cli.command()
@click.option("--before", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True, help="Snapshot taken before the change.")
@click.option("--after", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              required=True, help="Snapshot taken after the change.")
@click.option("--format", "fmt", type=click.Choice(["markdown", "json"]),
              default="markdown")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Write to file instead of stdout.")
def diff(before: Path, after: Path, fmt: str, output: Path | None) -> None:
    """Diff two snapshots, emitting a structured changelog.

    Use after a snapshot/reextract/snapshot cycle to see exactly what
    changed. Markdown output is human-readable and PR-pasteable; JSON is
    machine-readable for integrating with CI.
    """
    import dataclasses
    import json as _json

    from ingestion.snapshots import diff_snapshots, format_diff_markdown, read_snapshot

    summary = diff_snapshots(read_snapshot(before), read_snapshot(after))
    if fmt == "markdown":
        rendered = format_diff_markdown(summary)
    else:
        rendered = _json.dumps(dataclasses.asdict(summary), indent=2, default=str)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
        click.echo(f"diff written to {output}")
    else:
        click.echo(rendered)


@cli.command("regression-diff")
@click.option("--rule", "rule_filter", default=None,
              help="Restrict to this rule_id (snapshot, reextract, and diff).")
@click.option("--folder", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=None, help="Watch folder to reextract from. Defaults to WATCH_FOLDER.")
@click.option("--workdir", type=click.Path(path_type=Path), default=Path("./regression"),
              help="Where snapshot files and the diff land. Default ./regression.")
def regression_diff(rule_filter: str | None, folder: Path | None,
                    workdir: Path) -> None:
    """Snapshot, re-extract, snapshot again, diff. The full loop in one command.

    The before/after JSON files and the markdown diff are written under
    --workdir, so the result of every regression run is preserved as an
    artefact. Useful pre-merge before any rule / prompt / chunker change.
    """
    import datetime as _dt

    from ingestion.snapshots import (
        diff_snapshots,
        format_diff_markdown,
        snapshot_corpus,
        write_snapshot,
    )

    workdir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    before_path = workdir / f"snapshot-{stamp}-before.json"
    after_path  = workdir / f"snapshot-{stamp}-after.json"
    diff_path   = workdir / f"diff-{stamp}.md"

    click.echo("=> Capturing 'before' snapshot...")
    before = snapshot_corpus(rule_id=rule_filter)
    write_snapshot(before, before_path)
    click.echo(f"   {len(before['contracts'])} contracts -> {before_path}")

    click.echo("=> Re-extracting...")
    target = folder or get_settings().watch_folder
    pdfs = sorted(target.rglob("*.pdf"))
    ok = failed = 0
    for path in pdfs:
        if rule_filter:
            from rules.registry import resolve_rule_for_path
            rule = resolve_rule_for_path(path)
            if rule.rule_id != rule_filter:
                continue
        try:
            pipeline.process_file(path)
            ok += 1
        except Exception as e:
            click.echo(f"   FAIL {path.name}: {type(e).__name__}: {e}", err=True)
            failed += 1
    click.echo(f"   re-extract: {ok} ok, {failed} failed")

    click.echo("=> Capturing 'after' snapshot...")
    after = snapshot_corpus(rule_id=rule_filter)
    write_snapshot(after, after_path)
    click.echo(f"   {len(after['contracts'])} contracts -> {after_path}")

    click.echo("=> Diffing...")
    summary = diff_snapshots(before, after)
    diff_path.write_text(format_diff_markdown(summary))
    click.echo(f"   {summary.contracts_changed} changed, "
               f"{summary.contracts_added} added, "
               f"{summary.contracts_removed} removed, "
               f"{summary.contracts_unchanged} unchanged")
    click.echo(f"   diff -> {diff_path}")


@cli.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path),
                required=False)
@click.option("--rule", "rule_filter", default=None,
              help="Only re-extract files that resolve to this rule_id.")
def reextract(folder: Path | None, rule_filter: str | None) -> None:
    """Synchronously re-process every PDF under FOLDER (or WATCH_FOLDER).

    Bypasses the job queue. Re-runs extraction + chunking + embedding and
    upserts the contract / chunks. Use after a rule version bump or a chunker
    config change so existing documents reflect the new logic.
    """
    from rules.registry import resolve_rule_for_path

    target = folder or get_settings().watch_folder
    pdfs = sorted(target.rglob("*.pdf"))
    click.echo(f"found {len(pdfs)} pdfs under {target}")
    ok = 0
    failed = 0
    for path in pdfs:
        if rule_filter:
            rule = resolve_rule_for_path(path)
            if rule.rule_id != rule_filter:
                continue
        try:
            result = pipeline.process_file(path)
            click.echo(
                f"OK {path.name} -> rule={result.rule_id}/{result.rule_version} "
                f"chunks={result.num_chunks}"
            )
            ok += 1
        except Exception as e:
            click.echo(f"FAIL {path.name}: {type(e).__name__}: {e}", err=True)
            failed += 1
    click.echo(f"-- done: {ok} succeeded, {failed} failed")


if __name__ == "__main__":
    cli()
