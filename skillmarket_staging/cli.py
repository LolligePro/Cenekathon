"""DuckDB staging commands. No database server or credentials are required."""

import argparse
import json
import os
import sys

import duckdb

from .contracts import ContractError
from .companies import normalize_companies
from .database import database_status, export_database, import_parser, load_vacancies, publish_staged
from .db import initialize, inspect_database
from .ingest import ImportFailure, load_skills, receive_batch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=os.environ.get("SKILLMARKET_DB", "data/skillmarket.duckdb"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create the versioned staging schemas")
    companies = commands.add_parser("normalize-companies", help="Fast brand/group normalization of Parquet company names")
    companies.add_argument("sources", nargs="+")
    companies.add_argument("--output", default="data/company_map.parquet")
    companies.add_argument("--write-postings", help="Also write a compact posting_id -> company mapping")
    vacancies = commands.add_parser("load-vacancies", help="Load the seven-column original dataset")
    vacancies.add_argument("sources", nargs="+")
    publish = commands.add_parser("publish", help="Publish staged JSONL results and refresh monthly tables")
    sync = commands.add_parser("import-parser", help="Import a received JSONL or labeled-Parquet folder")
    sync.add_argument("directory")
    sync.add_argument("--parser-version", default="matching-v1")
    export = commands.add_parser("export-database", help="Write application tables as Parquet")
    export.add_argument("--directory", default="data")
    commands.add_parser("status", help="Show source, pending and parsed record counts")
    skills = commands.add_parser("load-skills", help="Register the shared fixed skill IDs")
    skills.add_argument("path")
    skills.add_argument("--taxonomy-version", required=True)
    receive = commands.add_parser("receive", help="Receive manifest.json and parsed.jsonl")
    receive.add_argument("batch_dir")
    receive.add_argument("--expected-ids", help="Optional UTF-8 file with one original input ID per line")
    for name in ["runs", "batches", "records"]:
        cmd = commands.add_parser(name)
        cmd.add_argument("--limit", type=int, default=20)
        if name == "records":
            cmd.add_argument("--batch-id", required=True)
    args = vars(parser.parse_args(argv))
    database, command = args.pop("database"), args.pop("command")
    try:
        if command == "init":
            result = initialize(database)
        elif command == "normalize-companies":
            result = normalize_companies(database, **args)
        elif command == "load-vacancies":
            result = load_vacancies(database, **args)
        elif command == "publish":
            result = publish_staged(database)
        elif command == "import-parser":
            result = import_parser(database, **args)
        elif command == "export-database":
            result = export_database(database, **args)
        elif command == "status":
            result = database_status(database)
        elif command == "load-skills":
            result = load_skills(database, **args)
        elif command == "receive":
            result = receive_batch(database, **args)
        else:
            result = inspect_database(database, command, **args)
    except ImportFailure as exc:
        print(json.dumps({"status": "failed", "run_id": exc.run_id,
                          "error_code": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (ContractError, OSError, duckdb.Error) as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0
