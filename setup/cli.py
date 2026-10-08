"""Command line interface: ``python setup.py <command>``."""

import argparse
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__
from . import alerts, jobs, kibana, loader, summary, teardown, templates, verify
from .client import ApiError, EsClient, KbClient, make_clients
from .config import Config, ConfigError, load_config
from .inject import SCENARIOS, active_scenarios, run_inject
from .logutil import LOG_FILE_NAME, get_logger, redact, say, setup_logging, warn
from .meta import is_loaded, jsonable, read_meta, write_meta
from .names import AllowlistError
from .preflight import run_preflight
from .timeutil import iso, utcnow

EXIT_OK, EXIT_FAIL, EXIT_PREFLIGHT = 0, 1, 2


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("connection (flags override env vars, .env and prompts)")
    g.add_argument("--kibana-url", help="Kibana URL (env ELASTIC_KIBANA_URL)")
    g.add_argument("--es-url", help="Elasticsearch URL (env ELASTIC_ES_URL; default: Kibana URL with .kb. -> .es.)")
    g.add_argument("--api-key", help="encoded API key (prefer env ELASTIC_API_KEY: flags are visible in process lists)")
    g.add_argument("--alert-email", help="also send rule alerts by email via a preconfigured connector (env ALERT_EMAIL)")
    g.add_argument("--allow-non-serverless", action="store_true", help="continue on a non-serverless cluster (untested)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug output (never includes keys or headers)")
    p.add_argument("--no-log-file", action="store_true", help="do not write %s" % LOG_FILE_NAME)
    return p


def build_parser() -> argparse.ArgumentParser:
    """The argparse tree (every subcommand supports --help)."""
    common = _common()
    parser = argparse.ArgumentParser(
        prog="python setup.py",
        description="ML workshop setup. With no command, runs 'setup'.",
    )
    parser.add_argument("--version", action="version", version="mlws-setup " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("setup", parents=[common], help="preflight, load data, jobs, alerts, Kibana assets (default)")
    p.add_argument("--reload", action="store_true", help="delete and regenerate data and ML jobs")
    p.add_argument("--days", type=int, help="backfill days (env MLWS_DAYS, default 28)")
    p.add_argument("--scale", type=float, help="volume multiplier (env MLWS_SCALE, default 1.0)")
    p.add_argument("--with-population", action="store_true", help="include the population job and its data")
    p.add_argument("--prebuild-module-job", action="store_true", help="pre-create the APM transaction-metrics job (mlws-apm_tx_metrics)")
    p.add_argument("--no-wait", action="store_true", help="do not wait for datafeeds to catch up (skips forecasts)")
    p.add_argument("--no-live-scenarios", action="store_true",
                   help="do not start the live problems (env MLWS_NO_LIVE=1); useful when re-running setup to repair something")
    p.add_argument("--yes", action="store_true", help="do not prompt for confirmation")
    p.add_argument("--workers", type=int, help="loader processes (default min(6, CPUs); 1 = in-process)")
    p.add_argument("--time-budget", type=float, default=20.0, help="load time budget in minutes (warns if exceeded)")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("preflight", parents=[common], help="check connectivity, project type and privileges")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("verify", parents=[common], help="check data, jobs and anomaly detection results")
    p.add_argument("--allow-pending", action="store_true", help="do not fail on jobs that are still catching up")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("inject", parents=[common], help="inject a live anomaly scenario")
    p.add_argument("scenario", nargs="?", default="cascade", choices=SCENARIOS, help="default: cascade")
    p.set_defaults(func=cmd_inject)

    p = sub.add_parser("teardown", parents=[common], help="remove every mlws artefact")
    p.add_argument("--yes", action="store_true", help="do not prompt for confirmation")
    p.set_defaults(func=cmd_teardown)

    p = sub.add_parser("links", parents=[common], help="print deep links into Kibana")
    p.set_defaults(func=cmd_links)

    p = sub.add_parser("anomalies", help="print the seeded anomalies")
    p.add_argument("--md", action="store_true", help="markdown table (docs/anomalies.md is generated from this)")
    p.add_argument("--t0", metavar="ISO", help="setup time (ISO 8601, UTC) to resolve actual windows; default: show D-offsets")
    p.set_defaults(func=cmd_anomalies)
    return parser


def _context(args: Any, need_key: bool = True) -> Tuple[Config, EsClient, KbClient]:
    setup_logging(verbose=getattr(args, "verbose", False))
    cfg = load_config(args, need_key=need_key)
    log_file = None if getattr(args, "no_log_file", False) else str(cfg.root / LOG_FILE_NAME)
    setup_logging(verbose=getattr(args, "verbose", False), log_file=log_file, secrets=[cfg.api_key])
    es, kb = make_clients(cfg)
    return cfg, es, kb


def cmd_preflight(args: Any) -> int:
    cfg, es, kb = _context(args)
    rep = run_preflight(es, kb, cfg.alert_email, args.allow_non_serverless)
    return EXIT_PREFLIGHT if rep.failed else EXIT_OK


def _step(steps: Dict[str, str], name: str, fn: Callable[[], Any]) -> bool:
    """Run one setup step, recording ok/FAILED without aborting the run."""
    try:
        fn()
        steps[name] = "ok"
        return True
    except (ApiError, OSError, ValueError, KeyError, AllowlistError) as exc:
        get_logger().error("ERROR in %s: %s", name, exc)
        steps[name] = "FAILED: %s" % exc
        return False


INTRO = (
    "This will set up a hands-on machine learning exercise in the Elastic project whose URL you give:",
    "  - installs the machine learning jobs, sample data, alert rules and dashboards (all names start with mlws-)",
    "  - starts a few problems that develop over the next hour, so there is something to find",
    "  - takes about 20 minutes, and is safe to run again",
    "  - remove everything later with: python setup.py teardown",
)


def _no_live(args: Any) -> bool:
    env = os.environ.get("MLWS_NO_LIVE", "").strip().lower() in ("1", "true", "yes", "on")
    return bool(getattr(args, "no_live_scenarios", False) or env)


def start_live_scenarios(es: EsClient, steps: Dict[str, str], skip_all: Optional[str],
                         skip_delayed: Optional[str]) -> Dict[str, str]:
    """Run cascade, info-flood and delayed (in that order) and record the "live scenarios" step.

    The windows never overlap: cascade and info-flood write 2-62 minutes into the future (different
    log streams: XYZ + APM versus ABC); delayed writes 45-30 minutes into the past and deletes nothing.
    Returns {scenario: outcome text}.
    """
    outcome: Dict[str, str] = {}
    if skip_all:
        steps["live scenarios"] = "skipped (%s)" % skip_all
        return outcome
    meta = read_meta(es)
    active = active_scenarios(meta)
    failed = False
    for name in SCENARIOS:
        if name == "delayed" and skip_delayed:
            outcome[name] = "skipped: " + skip_delayed
            say("Skipping live scenario 'delayed': %s." % skip_delayed)
        elif name in active:
            outcome[name] = "already running until %s UTC (not started again)" % active[name].replace("T", " ")[:16]
            say("Live scenario '%s' is already running (until %s UTC); not starting it again. Use --reload to start over."
                % (name, active[name].replace("T", " ")[:16]))
        else:
            try:
                rc = run_inject(es, name)
            except (ApiError, OSError, ValueError, KeyError, ImportError) as exc:
                get_logger().error("ERROR in live scenario %s: %s", name, exc)
                rc = 1
            if rc == 0:
                outcome[name] = "started"
            else:
                outcome[name] = "FAILED"
                failed = True
    started = [n for n, o in outcome.items() if o == "started"]
    parts = ["started " + ", ".join(started)] if started else []
    parts += ["%s %s" % (n, o) for n, o in outcome.items() if o != "started" and o != "FAILED"]
    if failed:
        steps["live scenarios"] = "FAILED: %s (run `python setup.py inject <name>` to retry)" % ", ".join(
            n for n, o in outcome.items() if o == "FAILED")
    else:
        steps["live scenarios"] = "ok (%s)" % "; ".join(parts)
    return outcome


def cmd_setup(args: Any) -> int:
    for line in INTRO:
        say(line)
    say("")
    cfg, es, kb = _context(args)
    if not args.yes and sys.stdin is not None and sys.stdin.isatty():
        say("Project: %s" % cfg.kibana_url)
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            say("Nothing was changed.")
            return EXIT_FAIL
    rep = run_preflight(es, kb, cfg.alert_email, args.allow_non_serverless)
    if rep.failed:
        say("")
        say("Setup stopped before changing anything. Read the [FAIL] lines above, follow the Fix, then run the command again.")
        return EXIT_PREFLIGHT
    steps: Dict[str, str] = {"preflight": "ok"}
    if not _step(steps, "templates", lambda: templates.load_templates(es, cfg.root)):
        return EXIT_FAIL

    meta = read_meta(es)
    prebuild = bool(args.prebuild_module_job or (meta or {}).get("prebuild_module_job"))
    if is_loaded(meta) and not args.reload:
        with_pop = bool(meta.get("with_population"))
        if args.with_population and not with_pop:
            warn("data was loaded without population data; run with --reload to add it (population job skipped)")
        steps["data"] = "skipped (already loaded; --reload to regenerate)"
        say("Data already loaded (t0 %s); skipping. Use --reload to regenerate." % meta.get("t0"))
    else:
        with_pop = bool(args.with_population)
        # build/validate the plan BEFORE deleting anything (import data, make_plan)
        built = _new_meta(cfg, with_pop, prebuild)
        if built is None:
            steps["data"] = "FAILED: data generator unavailable"
            return EXIT_FAIL
        if args.reload:
            if not args.yes and sys.stdin is not None and sys.stdin.isatty() and input("--reload deletes and regenerates all mlws data and ML jobs. Type 'yes': ").strip() != "yes":
                say("Aborted.")
                return EXIT_FAIL
            say("Reload: removing existing ML jobs, marker and data streams")
            teardown.remove_ml(es, cfg.root)
            teardown.delete_meta(es)
        teardown.remove_streams(es)  # clears partial earlier loads
        meta, plan = built
        write_meta(es, meta)
        result = loader.load(cfg.client_dict(), plan, args.workers, args.time_budget)
        if not loader.check_result(result):
            steps["data"] = "FAILED: see errors above (re-run setup to retry)"
            return EXIT_FAIL
        meta.update(loaded=True, loaded_at=iso(utcnow()), docs_loaded=result.stats.docs)
        write_meta(es, meta)
        steps["data"] = "ok (%s docs in %.0fs)" % (format(result.stats.docs, ","), result.seconds)
    _step(steps, "lifecycle", lambda: templates.set_lifecycle(es))

    specs = jobs.load_specs(cfg.root, with_pop, prebuild)
    _step(steps, "jobs", lambda: jobs.ensure_all(es, kb, specs, meta["start"]))
    _step(steps, "kibana", lambda: kibana.ensure_kibana(kb, cfg.root))
    caught_up = False
    delayed_skip: Optional[str] = "the jobs were not created"
    if specs and steps.get("jobs") == "ok":
        if args.no_wait:
            steps["datafeeds"] = "skipped (--no-wait); forecasts run on the next setup/verify cycle"
            delayed_skip = "--no-wait was given, so the jobs had not caught up with the data"
        else:
            caught = jobs.wait_for_catch_up(es, specs)
            caught_up = all(caught.values())
            steps["datafeeds"] = "ok" if caught_up else "pending (still catching up)"
            delayed_skip = None if caught_up else "the jobs had not finished catching up in time"
            _step(steps, "forecasts", lambda: jobs.run_forecasts(es, specs))
    # Rules go in last: created while datafeeds are still backfilling, the native ML rule
    # evaluates freshly written historical records and fires on weeks-old anomalies.
    _step(steps, "alerts", lambda: alerts.ensure_alerts(kb, cfg.root, cfg.alert_email, rep.email_connector_id))
    skip_all = None
    if _no_live(args):
        skip_all = "--no-live-scenarios" if getattr(args, "no_live_scenarios", False) else "MLWS_NO_LIVE is set"
    elif steps.get("jobs") != "ok":
        skip_all = "the jobs were not created"
    live = start_live_scenarios(es, steps, skip_all, delayed_skip)
    fresh = read_meta(es) or meta or {}
    summary.print_summary(cfg.kibana_url, steps, [s.job_id for s in specs], prebuild,
                          fresh.get("tail_end"), fresh.get("anomalies"), live, fresh.get("live_scenarios"))
    return EXIT_FAIL if any(v.startswith("FAILED") for v in steps.values()) else EXIT_OK


def _new_meta(cfg: Config, with_pop: bool, prebuild: bool) -> Optional[Tuple[Dict[str, Any], Any]]:
    """Build the plan and the (not yet loaded) meta document; None if data/ is unavailable."""
    try:
        from data import generate
    except ImportError as exc:
        warn("data generator not available: %s" % exc)
        return None
    plan = generate.make_plan(days=cfg.days, scale=cfg.scale, with_population=with_pop)
    meta = {
        "t0": iso(plan.t0), "start": iso(plan.start), "tail_end": iso(plan.tail_end),
        "seed": plan.seed, "days": cfg.days, "scale": cfg.scale, "version": __version__,
        "loaded": False, "with_population": with_pop, "prebuild_module_job": prebuild,
        "anomalies": jsonable(plan.anomalies),
    }
    return meta, plan


def cmd_verify(args: Any) -> int:
    cfg, es, kb = _context(args)
    return verify.run_verify(es, kb, cfg.root, args.allow_pending)


def cmd_inject(args: Any) -> int:
    cfg, es, kb = _context(args)
    return run_inject(es, args.scenario)


def cmd_teardown(args: Any) -> int:
    cfg, es, kb = _context(args)
    return teardown.run_teardown(es, kb, cfg.root, args.yes, sys.stdin is not None and sys.stdin.isatty())


def cmd_links(args: Any) -> int:
    cfg, _, _ = _context(args, need_key=False)
    summary.print_links(cfg.kibana_url)
    return EXIT_OK


def _offset_markdown(anoms: Any) -> str:
    """Markdown table with windows shown as offsets from setup day D (no cluster, no t0 needed)."""
    def jobs(lst: Any, key: str) -> str:
        if not lst:
            return "-"
        return "; ".join("`%s` / %s (%s %d)" % (c["job"], c["partition"],
                                                "score >=" if key == "min_score" else "score <=", c[key])
                         for c in lst)

    lines = ["| ID | Window (UTC) | Entity | What changed | Caught by | Must NOT be caught by |",
             "|---|---|---|---|---|---|"]
    for a in anoms:
        if a.day_offset is not None and a.start_hhmm:
            win = "D%+d %s, %d min" % (a.day_offset, a.start_hhmm, a.duration_min)
        else:
            win = "live, %d min" % a.duration_min
        title = a.id + (" (population only)" if a.requires == "with_population" else "")
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            title, win, a.entity or "-", (a.what or a.title).replace("|", "/"),
            jobs(a.catches, "min_score"), jobs(a.must_not_catch, "max_score")))
    return "\n".join(lines) + "\n"


def cmd_anomalies(args: Any) -> int:
    try:
        from data import anomalies
    except ImportError as exc:
        print("data generator not available: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    anoms = anomalies.SEEDED
    resolved = None
    if getattr(args, "t0", None):
        try:
            resolved = anomalies.resolve(anoms, anomalies._parse(args.t0))
        except ValueError:
            print("Invalid --t0 %r: use an ISO 8601 time such as 2026-09-30T12:00:00Z" % args.t0, file=sys.stderr)
            return EXIT_PREFLIGHT
    if args.md:
        print(anomalies.anomalies_markdown(resolved) if resolved is not None else _offset_markdown(anoms))
    elif resolved is not None:
        for a in resolved:
            print("%s  %s  %s to %s UTC" % (a.id, a.title, a.start.strftime("%Y-%m-%d %H:%M"),
                                            a.end.strftime("%Y-%m-%d %H:%M")))
    else:
        for a in anoms:
            print("%s  %s" % (a.id, a.title))
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point; returns the process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "setup")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return EXIT_PREFLIGHT
    except AllowlistError as exc:
        get_logger().error("ABORT: %s", exc)
        return EXIT_FAIL
    except ApiError as exc:
        get_logger().error("ERROR: %s", exc)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # last resort: never leak a raw traceback or key
        log = get_logger()
        log.error("ERROR: unexpected %s", redact(repr(exc)))
        if getattr(args, "verbose", False):
            log.debug("traceback:", exc_info=True)  # formatter redacts secrets
        return EXIT_FAIL
