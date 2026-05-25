"""CLI for Agent Evaluation Harness."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_config_from_env
from .models import EvalStore
from .runner import EvalRunner, import_suite_from_yaml, load_suite_from_yaml
from .reporter import generate_single_run_report, generate_comparison_report, export_results_csv


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-eval", description="RAGFlow Agent Evaluation Harness")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    sub = parser.add_subparsers(dest="command")

    # --- suite commands ---
    suite_p = sub.add_parser("suite", help="Manage test suites")
    suite_sub = suite_p.add_subparsers(dest="suite_cmd")

    # suite list
    suite_sub.add_parser("list", help="List all test suites")

    # suite create
    create_p = suite_sub.add_parser("create", help="Import suite from YAML")
    create_p.add_argument("path", help="Path to YAML file")

    # suite show
    show_p = suite_sub.add_parser("show", help="Show suite details")
    show_p.add_argument("suite_id", help="Suite ID")

    # suite delete
    del_p = suite_sub.add_parser("delete", help="Delete suite")
    del_p.add_argument("suite_id", help="Suite ID")

    # suite validate
    val_p = suite_sub.add_parser("validate", help="Validate YAML without importing")
    val_p.add_argument("path", help="Path to YAML file")

    # --- run command ---
    run_p = sub.add_parser("run", help="Run evaluation")
    run_p.add_argument("suite_id", help="Suite ID to evaluate")
    run_p.add_argument("--agent-title", help="Target agent by title")
    run_p.add_argument("--agent-id", help="Target agent by ID")
    run_p.add_argument("--dialog-id", help="Target dialog by ID")
    run_p.add_argument("--name", default="", help="Run name")
    run_p.add_argument("--tags", help="Comma-separated tag filter")
    run_p.add_argument("--timeout", type=int, help="Per-case timeout in seconds")
    run_p.add_argument("--skip-judge", action="store_true", help="Skip LLM-as-judge metrics")

    # --- list runs ---
    list_p = sub.add_parser("list", help="List evaluation runs")
    list_p.add_argument("--suite-id", help="Filter by suite ID")

    # --- show run ---
    show_run_p = sub.add_parser("show", help="Show run details and results")
    show_run_p.add_argument("run_id", help="Run ID")

    # --- compare ---
    cmp_p = sub.add_parser("compare", help="Compare multiple runs")
    cmp_p.add_argument("run_ids", nargs="+", help="Run IDs to compare")

    # --- export ---
    exp_p = sub.add_parser("export", help="Export run results")
    exp_p.add_argument("run_id", help="Run ID")
    exp_p.add_argument("--format", choices=["json", "csv"], default="json", help="Export format")
    exp_p.add_argument("--output", "-o", help="Output file path")

    # --- serve ---
    srv_p = sub.add_parser("serve", help="Start REST API server")
    srv_p.add_argument("--host", default=None, help="Bind host")
    srv_p.add_argument("--port", type=int, default=None, help="Bind port")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    _setup_logging(args.verbose)
    config = load_config_from_env()
    store = EvalStore(config.db_path)

    if args.command == "suite":
        _handle_suite(args, store, config)
    elif args.command == "run":
        _handle_run(args, store, config)
    elif args.command == "list":
        _handle_list(args, store)
    elif args.command == "show":
        _handle_show(args, store)
    elif args.command == "compare":
        _handle_compare(args, store)
    elif args.command == "export":
        _handle_export(args, store)
    elif args.command == "serve":
        _handle_serve(args, config)


def _handle_suite(args, store: EvalStore, config) -> None:
    if args.suite_cmd == "list":
        suites = store.list_suites()
        if not suites:
            print("No suites found.")
            return
        for s in suites:
            print(f"  {s['suite_id'][:12]}  {s['name']}  v{s['version']}  ({s['created_at'][:10]})")

    elif args.suite_cmd == "create":
        suite_id = import_suite_from_yaml(store, args.path)
        cases = store.get_cases(suite_id)
        print(f"Created suite {suite_id[:12]} with {len(cases)} cases")

    elif args.suite_cmd == "show":
        suite = store.get_suite(args.suite_id)
        if not suite:
            print(f"Suite not found: {args.suite_id}")
            sys.exit(1)
        print(json.dumps(suite, indent=2, ensure_ascii=False))
        cases = store.get_cases(args.suite_id)
        print(f"\n  Cases: {len(cases)}")
        for c in cases:
            print(f"    {c['case_id'][:12]}  {c['question'][:60]}")

    elif args.suite_cmd == "delete":
        if store.delete_suite(args.suite_id):
            print(f"Deleted suite: {args.suite_id}")
        else:
            print(f"Suite not found: {args.suite_id}")

    elif args.suite_cmd == "validate":
        try:
            data = load_suite_from_yaml(args.path)
            print(f"Valid suite: '{data['name']}' with {len(data['cases'])} cases")
        except ValueError as e:
            print(f"Validation failed: {e}")
            sys.exit(1)


def _handle_run(args, store: EvalStore, config) -> None:
    tag_filter = args.tags.split(",") if args.tags else None
    runner = EvalRunner(config, store)

    print(f"Running evaluation on suite {args.suite_id[:12]}...")
    result = runner.run(
        suite_id=args.suite_id,
        agent_title=args.agent_title,
        agent_id=args.agent_id,
        dialog_id=args.dialog_id,
        name=args.name,
        tag_filter=tag_filter,
        timeout_sec=args.timeout,
        skip_judge=args.skip_judge,
    )

    print(f"\nRun {result.run_id[:12]} - Status: {result.status}")
    summary = result.metrics_summary
    if summary:
        print(f"  Pass rate: {summary.get('pass_rate', 0):.1%}")
        print(f"  Cases: {summary.get('total_cases', 0)} passed, {summary.get('errors', 0)} errors")
        if "avg_composite_score" in summary:
            print(f"  Avg composite score: {summary['avg_composite_score']:.3f}")
        if "avg_execution_time_sec" in summary:
            print(f"  Avg execution time: {summary['avg_execution_time_sec']:.1f}s")


def _handle_list(args, store: EvalStore) -> None:
    runs = store.list_runs(suite_id=args.suite_id)
    if not runs:
        print("No runs found.")
        return
    for r in runs:
        agent = r.get("agent_title") or r.get("dialog_id") or "?"
        print(f"  {r['run_id'][:12]}  {r['status']:10s}  agent={agent[:30]}  ({r['created_at'][:10]})")


def _handle_show(args, store: EvalStore) -> None:
    report = generate_single_run_report(store, args.run_id)
    print(json.dumps(report, indent=2, ensure_ascii=False))


def _handle_compare(args, store: EvalStore) -> None:
    report = generate_comparison_report(store, args.run_ids)
    print(json.dumps(report, indent=2, ensure_ascii=False))


def _handle_export(args, store: EvalStore) -> None:
    if args.format == "csv":
        content = export_results_csv(store, args.run_id)
    else:
        report = generate_single_run_report(store, args.run_id)
        content = json.dumps(report, indent=2, ensure_ascii=False)

    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
        print(f"Exported to {args.output}")
    else:
        print(content)


def _handle_serve(args, config) -> None:
    from .server import start_server
    host = args.host or config.server_host
    port = args.port or config.server_port
    print(f"Starting eval harness server on {host}:{port}")
    start_server(config, host=host, port=port)


if __name__ == "__main__":
    main()
