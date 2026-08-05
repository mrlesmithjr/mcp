"""CLI for obsidian-search-tools.

Mirrors the MCP tools for command-line use and provides a configure subcommand
that validates environment variable setup.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from obsidian_search_tools.config import (
    get_excluded_sections,
    get_reindex_staleness_hours,
    get_reindex_times,
    get_vault_path,
)

app = typer.Typer(
    name="obsidian-search-tools",
    help="Hybrid semantic + keyword search over an Obsidian vault.",
    no_args_is_help=True,
)
db_app = typer.Typer(help="Database management commands.")
app.add_typer(db_app, name="db")
schedule_app = typer.Typer(help="Reindex LaunchAgent scheduling commands.")
app.add_typer(schedule_app, name="schedule")


# ---------------------------------------------------------------------------
# obsidian-search-tools configure
# ---------------------------------------------------------------------------


@app.command()
def configure(
    write: bool = typer.Option(
        False,
        "--write/--no-write",
        help="Write ~/.config/obsidian-search-tools/env with current env vars so they survive plugin updates.",
    ),
) -> None:
    """Validate OBSIDIAN_VAULT_PATH and report discovered sections.

    Without --write: prints what would be indexed vs excluded and shows what
    the env file would contain. With --write: also persists env vars to
    ~/.config/obsidian-search-tools/env so they survive plugin updates.
    """
    vault_path = get_vault_path()
    if vault_path is None:
        typer.echo("ERROR: OBSIDIAN_VAULT_PATH is not set.", err=True)
        typer.echo("Export it before running configure:", err=True)
        typer.echo("  export OBSIDIAN_VAULT_PATH=/path/to/your/vault", err=True)
        raise typer.Exit(code=1)

    if not vault_path.is_dir():
        typer.echo(f"ERROR: OBSIDIAN_VAULT_PATH does not exist or is not a directory: {vault_path}", err=True)
        raise typer.Exit(code=1)

    excluded = get_excluded_sections()
    all_subdirs = sorted(d.name for d in vault_path.iterdir() if d.is_dir() and not d.name.startswith("."))
    included = [d for d in all_subdirs if d not in excluded]
    skipped = [d for d in all_subdirs if d in excluded]

    typer.echo(f"Vault path : {vault_path}")
    typer.echo(f"Sections to index : {', '.join(included) if included else '(none found)'}")
    if skipped:
        typer.echo(f"Excluded sections : {', '.join(skipped)}")
    else:
        typer.echo("Excluded sections : (none)")
    typer.echo(f"Reindex schedule : {', '.join(get_reindex_times())}")
    typer.echo(f"Staleness guard   : {get_reindex_staleness_hours()}h")

    # Build env file content from current env vars. Reindex schedule vars are
    # only written if already present in the environment, so re-running
    # --write does not clobber a schedule set directly in the persisted env
    # file (this function only reads os.environ, never the file itself).
    config_dir = Path.home() / ".config" / "obsidian-search-tools"
    env_path = config_dir / "env"
    env_lines = [f"OBSIDIAN_VAULT_PATH={vault_path}"]
    if excluded:
        env_lines.append(f"OBSIDIAN_EXCLUDED_SECTIONS={','.join(sorted(excluded))}")
    reindex_times_raw = os.environ.get("OBSIDIAN_REINDEX_TIMES", "").strip()
    if reindex_times_raw:
        env_lines.append(f"OBSIDIAN_REINDEX_TIMES={reindex_times_raw}")
    staleness_raw = os.environ.get("OBSIDIAN_REINDEX_STALENESS_HOURS", "").strip()
    if staleness_raw:
        env_lines.append(f"OBSIDIAN_REINDEX_STALENESS_HOURS={staleness_raw}")
    env_content = "\n".join(env_lines) + "\n"

    if write:
        config_dir.mkdir(parents=True, exist_ok=True)
        env_path.write_text(env_content, encoding="utf-8")
        typer.echo(f"Written: {env_path}")
    else:
        typer.echo("")
        typer.echo(f"Would write to {env_path}:")
        for line in env_lines:
            typer.echo(f"  {line}")
        typer.echo("")
        typer.echo("Run with --write to persist these values, or edit the file manually.")


# ---------------------------------------------------------------------------
# obsidian-search-tools reindex
# ---------------------------------------------------------------------------


@app.command()
def reindex(
    force: bool = typer.Option(
        False, "--force", help="Force a full rebuild, skipping incremental mtime-based detection."
    ),
    skip_if_fresh: bool = typer.Option(
        False,
        "--skip-if-fresh",
        help=(
            "Skip the rebuild if the index was refreshed within the staleness window "
            "(OBSIDIAN_REINDEX_STALENESS_HOURS, default 2h). Used by the scheduled "
            "reindex job so RunAtLoad and a nearby StartCalendarInterval fire don't "
            "double-embed the vault."
        ),
    ),
) -> None:
    """Rebuild the vault search index from OBSIDIAN_VAULT_PATH."""
    vault_path = get_vault_path()
    if vault_path is None:
        typer.echo("ERROR: OBSIDIAN_VAULT_PATH is not set.", err=True)
        raise typer.Exit(code=1)

    if not vault_path.is_dir():
        typer.echo(f"ERROR: Not a directory: {vault_path}", err=True)
        raise typer.Exit(code=1)

    if skip_if_fresh:
        from obsidian_search_tools.scheduler import get_last_reindex, is_stale

        staleness_hours = get_reindex_staleness_hours()
        last_reindex = get_last_reindex()
        if not is_stale(last_reindex, staleness_hours):
            typer.echo(
                f"Skipping reindex: index was last refreshed at {last_reindex} "
                f"(within the {staleness_hours}h staleness window)."
            )
            return

    excluded = get_excluded_sections()

    from obsidian_search_tools.indexer import build_index

    typer.echo(f"Indexing vault at: {vault_path}")
    report = build_index(vault_path, excluded, force=force)
    typer.echo(
        f"Done in {report.elapsed_seconds}s -- "
        f"{report.notes_indexed} indexed, {report.notes_unchanged} unchanged, "
        f"{report.notes_deleted} deleted, {report.chunks_indexed} chunks, "
        f"sections: {', '.join(report.sections)}"
    )


# ---------------------------------------------------------------------------
# obsidian-search-tools status
# ---------------------------------------------------------------------------


@app.command()
def status() -> None:
    """Show index status (note count, chunk count, last reindex time, sections)."""
    from obsidian_search_tools.mcp_server import vault_status

    result = json.loads(vault_status())
    if "error" in result:
        typer.echo(f"ERROR: {result['error']}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Note count     : {result['note_count']}")
    typer.echo(f"Chunk count    : {result['chunk_count']}")
    typer.echo(f"Last reindex   : {result['last_reindex'] or 'never'}")
    typer.echo(f"Model          : {result['model_id'] or 'unknown'}")
    typer.echo(f"Sections       : {', '.join(result['indexed_sections']) or '(none)'}")
    typer.echo(f"DB path        : {result['db_path']}")


# ---------------------------------------------------------------------------
# schedule subcommand (reindex LaunchAgent cadence)
# ---------------------------------------------------------------------------


@schedule_app.command("show")
def schedule_show() -> None:
    """Print the resolved reindex schedule, staleness threshold, and current staleness."""
    from obsidian_search_tools.scheduler import get_last_reindex, is_stale

    times = get_reindex_times()
    staleness_hours = get_reindex_staleness_hours()
    last_reindex = get_last_reindex()

    typer.echo(f"Reindex times      : {', '.join(times)}")
    typer.echo(f"Staleness threshold: {staleness_hours}h")
    typer.echo(f"Last reindex       : {last_reindex or 'never'}")
    typer.echo(f"Currently stale    : {is_stale(last_reindex, staleness_hours)}")


@schedule_app.command("render")
def schedule_render(
    target: Path = typer.Argument(..., help="Rendered plist path to patch in place (after __HOME__ substitution)."),
) -> None:
    """Fill in the StartCalendarInterval block of a rendered reindex plist.

    Called by launchagents/render.sh during plugin install/update. Reads the
    cadence from OBSIDIAN_REINDEX_TIMES (or the packaged default) so the
    installer and `schedule show` share one source of truth.
    """
    from obsidian_search_tools.scheduler import render_plist

    if not target.is_file():
        typer.echo(f"ERROR: not a file: {target}", err=True)
        raise typer.Exit(code=1)

    times = get_reindex_times()
    text = target.read_text(encoding="utf-8")
    rendered = render_plist(text, times)
    target.write_text(rendered, encoding="utf-8")
    typer.echo(f"Rendered StartCalendarInterval ({', '.join(times)}) into {target}")


# ---------------------------------------------------------------------------
# db subcommand (schema init / verify)
# ---------------------------------------------------------------------------


@db_app.command("init")
def db_init() -> None:
    """Initialize (or verify) the database schema."""
    from obsidian_search_tools.db import connect, get_db_path

    db_path = get_db_path()
    conn = connect(db_path)
    conn.close()
    typer.echo(f"Database initialized at: {db_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app()


if __name__ == "__main__":
    main()
