"""Command line interface: python -m ufo_pipeline <command>."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .pipeline import evaluate_and_reassess, run_analysis, write_report
from .provenance import IntegrityError, load_json, write_json
from .synthetic import SCENARIOS, write_dataset
from .zenodo import ZenodoError, load_manifest, retrieve_dataset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ufo_pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-manifest", help="check the dataset manifest (no network)")
    p.add_argument("--manifest", default="data/manifest/datasets.json")

    p = sub.add_parser("fetch", help="download + verify datasets from Zenodo (needs network)")
    p.add_argument("--manifest", default="data/manifest/datasets.json")
    p.add_argument("--dest", default="data/raw")
    p.add_argument("--provenance-dir", default="provenance")
    p.add_argument("--dataset", help="only this dataset_id")
    p.add_argument("--allow-unpinned", action="store_true",
                   help="exit 0 for datasets that downloaded and passed the source checksum but have no "
                        "SHA-256 pin yet (first fetch). Failed datasets still exit 1; unpinned is never verified.")

    p = sub.add_parser("analyze", help="run one analysis config")
    p.add_argument("--config", required=True)
    p.add_argument("--out", default="results")
    p.add_argument("--base-dir", default=".")

    p = sub.add_parser("replicate", help="evaluate an independent replication against an original report")
    p.add_argument("--original", required=True)
    p.add_argument("--replication", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("synthetic", help="generate a synthetic TEST dataset (never evidence)")
    p.add_argument("--scenario", choices=SCENARIOS, required=True)
    p.add_argument("--out", default="data/synthetic")
    p.add_argument("--seed", type=int, default=0)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate-manifest":
            entries = load_manifest(args.manifest)
            print(f"manifest OK: {len(entries)} dataset(s)")
            return 0
        if args.command == "fetch":
            entries = load_manifest(args.manifest)
            if args.dataset:
                entries = [e for e in entries if e["dataset_id"] == args.dataset]
                if not entries:
                    print(f"dataset {args.dataset!r} not in manifest", file=sys.stderr)
                    return 2
            if not entries:
                print("manifest lists no datasets: nothing to fetch (add real Zenodo records first)")
                return 0
            failures = 0
            for entry in entries:
                prov_path = Path(args.provenance_dir) / f"{entry['dataset_id']}.json"
                existing = load_json(prov_path) if prov_path.exists() else None
                prov = retrieve_dataset(entry, Path(args.dest) / entry["dataset_id"], existing=existing)
                write_json(prov_path, prov)
                print(f"{entry['dataset_id']}: {prov['status']}")
                accepted = ("verified", "unpinned") if args.allow_unpinned else ("verified",)
                failures += prov["status"] not in accepted
            return 1 if failures else 0
        if args.command == "analyze":
            report = run_analysis(load_json(args.config), base_dir=args.base_dir)
            paths = write_report(report, args.out)
            a = report["assessment"]
            print(f"{a['status']} | positive evidence: {a['counts_as_positive_evidence']} | {paths['json']}")
            return 0
        if args.command == "replicate":
            result = evaluate_and_reassess(load_json(args.original), load_json(args.replication))
            write_json(args.out, result)
            after = result["original_assessment_after"]
            print(f"replication: {result['replication']['outcome']} | original now: {after['status']} "
                  f"| positive evidence: {after['counts_as_positive_evidence']}")
            return 0
        if args.command == "synthetic":
            paths = write_dataset(args.scenario, args.out, seed=args.seed)
            print(f"wrote {paths['csv']} and {paths['provenance']} (SYNTHETIC - never evidence)")
            return 0
    except IntegrityError as exc:
        print(f"INTEGRITY ERROR: {exc}", file=sys.stderr)
        return 2
    except (ZenodoError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0
