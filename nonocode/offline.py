from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from nonocode.config import ConfigError, load_config
from nonocode.memory.auto_memory import MemoryManager
from nonocode.memory.session import SessionManager
from nonocode.product import ProductService


def run_offline(args) -> None:
    sessions = SessionManager(os.getcwd())
    product = ProductService(sessions,MemoryManager(os.getcwd()))
    if args.history is not None:
        print(json.dumps(product.history(args.history),ensure_ascii=False,indent=2))
    elif args.runs:
        print(json.dumps(sessions.database.list_runs(),ensure_ascii=False,indent=2))
    else:
        report = {'version':'0.3.0','python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,
                  'journal_mode':sessions.database.journal_mode,'database':str(sessions.database.path),'workspace':os.getcwd(),
                  'history_search':'FTS5 + short-query fallback' if sessions.database.fts else 'substring search',
                  'sessions':len(sessions.list())}
        try:
            config = load_config()
            report['configuration'] = 'valid'
            report['providers'] = [{'name':p.name,'model':p.model,'protocol':p.protocol,
                                    'api_key_present':bool(p.resolve_api_key())} for p in config.providers]
        except ConfigError:
            report['configuration'] = 'missing or invalid; configure .nonocode/config.yaml'
        print(json.dumps(report,ensure_ascii=False,indent=2))
