"""`healthtracker` command line: setup, jobs, approvals, demo."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import typer

from healthtracker.config import get_settings
from healthtracker.db import connect, execute, fetch_all, fetch_one, use_database

app = typer.Typer(help="Personal Health & Training System", no_args_is_help=True, add_completion=False)
proposals_app = typer.Typer(help="Review rule changes proposed by the weekly job", no_args_is_help=True)
rules_app = typer.Typer(help="Inspect and change rule status", no_args_is_help=True)
app.add_typer(proposals_app, name="proposals")
app.add_typer(rules_app, name="rules")


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@app.command("init-db")
def init_db(database_url: str = typer.Option(None, help="Defaults to DATABASE_URL")) -> None:
    """Create the database if needed, apply migrations, and set the profile time zone."""
    from healthtracker.migrate import ensure_database, migrate

    s = get_settings()
    url = database_url or s.database_url
    if ensure_database(url):
        typer.echo("created database")
    applied = migrate(url)
    typer.echo(f"migrations applied: {applied or 'none pending'}")
    with connect(url) as conn:
        execute(conn, "UPDATE core.profile SET timezone = %s WHERE id = 1 AND timezone = 'UTC'", (s.tz,))


@app.command()
def migrate() -> None:
    """Apply pending SQL migrations."""
    from healthtracker.migrate import migrate as run

    typer.echo(run() or "nothing to apply")


@app.command("garmin-login")
def garmin_login(email: str = typer.Option(None)) -> None:
    """One-time Garmin Connect login (asks for password and MFA; stores tokens only)."""
    from healthtracker.ingest.garmin import login_interactive

    _setup_logging()
    login_interactive(email)
    typer.echo(f"Tokens saved to {get_settings().garmin_tokenstore}")


@app.command("garmin-backfill")
def garmin_backfill(days: int = typer.Option(90, help="How many days back to fetch"),
                    end: str = typer.Option(None, help="Last date (YYYY-MM-DD), default today")) -> None:
    """Fetch Garmin history into raw (then run `backfill` to rebuild everything)."""
    from healthtracker.ingest.garmin import pull

    _setup_logging()
    stop = _date(end) or date.today()
    n = pull(stop - timedelta(days=days - 1), stop)
    typer.echo(f"stored {n} new raw payloads")


@app.command("import-fit")
def import_fit(paths: list[Path] = typer.Argument(..., help="FIT files or folders")) -> None:
    """Load FIT files exported from Garmin Connect (the fallback when the API breaks)."""
    from healthtracker.ingest.fit import import_fit_files

    files = []
    for p in paths:
        files += sorted(p.glob("*.fit")) + sorted(p.glob("*.FIT")) if p.is_dir() else [p]
    typer.echo(f"stored {import_fit_files(files)} new FIT payloads from {len(files)} files")


@app.command()
def nightly(day: str = typer.Option(None, "--date", help="Day to run for (default: today, local)"),
            skip_ingest: bool = typer.Option(False, help="Don't call Garmin"),
            no_llm: bool = typer.Option(False, help="Use the template instead of the LLM"),
            verbose: bool = False) -> None:
    """Run the daily pipeline (ingest -> features -> grade -> rules -> recommendation -> message)."""
    from healthtracker.pipeline.jobs import nightly as run

    _setup_logging(verbose)
    report = run(_date(day), skip_ingest=skip_ingest, use_llm=False if no_llm else None)
    typer.echo(report.summary())
    if not report.ok:
        raise typer.Exit(1)


@app.command()
def weekly(day: str = typer.Option(None, "--date"), verbose: bool = False) -> None:
    """Run the weekly learning loop (insights, tuning proposals, experiments, next week's plan)."""
    from healthtracker.pipeline.jobs import weekly as run

    _setup_logging(verbose)
    report = run(_date(day))
    typer.echo(report.summary())
    if not report.ok:
        raise typer.Exit(1)


@app.command()
def backfill(start: str = typer.Option(..., help="First day (YYYY-MM-DD)"),
             end: str = typer.Option(None, help="Last day, default yesterday"),
             llm_days: int = typer.Option(0, help="Use the LLM for the last N days"),
             verbose: bool = False) -> None:
    """Rebuild core, features, flags, recommendations, and grades for a date range."""
    from healthtracker.pipeline.jobs import backfill as run

    _setup_logging(verbose)
    report = run(date.fromisoformat(start), _date(end) or date.today() - timedelta(days=1), llm_days=llm_days)
    typer.echo(report.summary())


@app.command()
def demo(days: int = typer.Option(180, help="Days of synthetic history"),
         llm_days: int = typer.Option(0, help="Phrase the last N days with the LLM"),
         keep: bool = typer.Option(False, help="Reuse the demo database if it exists")) -> None:
    """Build a demo database full of synthetic data (never touches your real database)."""
    import numpy as np

    from healthtracker.migrate import drop_database, ensure_database, migrate
    from healthtracker.pipeline.jobs import backfill as run_backfill
    from healthtracker.sample.generator import generate

    _setup_logging()
    s = get_settings()
    url = s.demo_database_url
    if url == s.database_url:
        raise typer.BadParameter("DEMO_DATABASE_URL must differ from DATABASE_URL")
    if keep and not ensure_database(url):
        typer.echo("demo database exists; keeping it")
        return
    drop_database(url)
    ensure_database(url)
    migrate(url)
    use_database(url)
    with connect() as conn:
        counts = generate(conn, days=days)
    typer.echo(f"generated {counts}")
    report = run_backfill(counts["start"], counts["end"], llm_days=llm_days)
    typer.echo(report.summary())
    with connect() as conn:  # a few ratings, as if entered in the app
        rng = np.random.default_rng(3)
        for r in fetch_all(conn, "SELECT id FROM analytics.recommendation WHERE kind = 'daily'"):
            if rng.random() < 0.4:
                execute(conn, "UPDATE analytics.recommendation SET rating = %s WHERE id = %s",
                        (int(rng.choice([3, 4, 4, 5, 5, 2])), r["id"]))
    typer.echo(f"demo ready in {url.rsplit('/', 1)[-1]}")


@app.command()
def experiment(hypothesis_id: int, start: str = typer.Option(None), days: int = 14,
               seed: int = typer.Option(None)) -> None:
    """Start a randomized self-experiment for a hypothesis."""
    from healthtracker.stats.experiments import create_experiment

    with connect() as conn:
        eid = create_experiment(conn, hypothesis_id, _date(start) or date.today(), days, seed)
    typer.echo(f"experiment {eid} created")


@app.command()
def status(limit: int = 15) -> None:
    """Recent pipeline runs."""
    with connect() as conn:
        rows = fetch_all(conn, "SELECT * FROM analytics.pipeline_run ORDER BY id DESC LIMIT %s", (limit,))
    for r in rows:
        err = (r["error"] or "").splitlines()[0][:80] if r["error"] else ""
        typer.echo(f"{r['started_at']:%Y-%m-%d %H:%M} {r['job']:8} {r['step'] or '':16} "
                   f"{r['status'] or 'running':7} rows={r['rows_written'] or 0} {err}")


@proposals_app.command("list")
def proposals_list() -> None:
    from healthtracker.stats.tuning import pending_parameter_proposals

    with connect() as conn:
        params = pending_parameter_proposals(conn)
        status_changes = fetch_all(conn, "SELECT * FROM analytics.rule_status_change WHERE approved IS NULL")
    for p in params:
        typer.echo(f"param #{p['id']}: {p['rule_id']}.{p['name']} {p['old_value']} -> {p['new_value']}  {p['reason']}")
    for c in status_changes:
        typer.echo(f"status #{c['id']}: {c['rule_id']} {c['old_status']} -> {c['new_status']}  {c['reason']}")
    if not params and not status_changes:
        typer.echo("nothing pending")


@proposals_app.command("approve")
def proposals_approve(kind: str = typer.Argument(..., help="param or status"), proposal_id: int = typer.Argument(...)) -> None:
    _decide(kind, proposal_id, True)


@proposals_app.command("reject")
def proposals_reject(kind: str = typer.Argument(..., help="param or status"), proposal_id: int = typer.Argument(...)) -> None:
    _decide(kind, proposal_id, False)


def _decide(kind: str, proposal_id: int, approve: bool) -> None:
    from healthtracker.stats.promotion import decide_status
    from healthtracker.stats.tuning import decide_parameter

    with connect() as conn:
        row = decide_parameter(conn, proposal_id, approve) if kind == "param" else decide_status(conn, proposal_id, approve)
    typer.echo(f"{'approved' if approve else 'rejected'}: {row['rule_id']}")


@rules_app.command("list")
def rules_list() -> None:
    with connect() as conn:
        rows = fetch_all(conn, """SELECT r.rule_id, r.status, r.min_history_days, p.n_actions, p.followed_rate,
                                         p.avg_outcome_when_followed, p.avg_outcome_when_not
                                  FROM analytics.rule r LEFT JOIN analytics.v_rule_performance p USING (rule_id)
                                  ORDER BY r.rule_id""")
    for r in rows:
        perf = (f"n={r['n_actions']} followed={r['followed_rate']:.0%} "
                f"outcome {r['avg_outcome_when_followed'] or 0:+.2f} vs {r['avg_outcome_when_not'] or 0:+.2f}"
                if r["n_actions"] else "no graded actions")
        typer.echo(f"{r['rule_id']:26} {r['status']:8} {perf}")


@rules_app.command("set")
def rules_set(rule_id: str, status_: str = typer.Argument(..., metavar="STATUS", help="shadow|active|retired")) -> None:
    from healthtracker.stats.promotion import set_status

    with connect() as conn:
        if not fetch_one(conn, "SELECT 1 FROM analytics.rule WHERE rule_id = %s", (rule_id,)):
            raise typer.BadParameter(f"unknown rule {rule_id}")
        set_status(conn, rule_id, status_)
    typer.echo(f"{rule_id} -> {status_}")


if __name__ == "__main__":
    app()
