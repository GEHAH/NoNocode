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
    if getattr(args, 'restore_backup', None):
        from nonocode.storage.recovery import restore_bundle
        print(restore_bundle(Path(args.restore_backup),Path(args.restore_to)))
        return
    sessions = SessionManager(os.getcwd())
    product = ProductService(sessions,MemoryManager(os.getcwd()))
    if getattr(args,'artifacts',None) is not None:
        from nonocode.storage.products import ProductStore
        print(json.dumps(ProductStore(sessions.database).artifacts(args.artifacts),ensure_ascii=False,indent=2))
    elif getattr(args,'sources',None) is not None:
        from nonocode.storage.products import ProductStore
        print(json.dumps(ProductStore(sessions.database).sources(args.sources),ensure_ascii=False,indent=2))
    elif getattr(args,'usage',None) is not None:
        from nonocode.execution import usage_summary
        print(json.dumps(usage_summary(sessions.database,args.usage),ensure_ascii=False,indent=2))
    elif getattr(args, 'backup', None):
        from nonocode.storage.recovery import backup_bundle
        print(backup_bundle(sessions.database,Path(args.backup)))
    elif args.history is not None:
        print(json.dumps(product.history(args.history),ensure_ascii=False,indent=2))
    elif args.runs:
        print(json.dumps(sessions.database.list_runs(),ensure_ascii=False,indent=2))
    else:
        report = {'version':'0.6.0','python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,
                  'journal_mode':sessions.database.journal_mode,'database':str(sessions.database.path),'workspace':os.getcwd(),
                  'history_search':'FTS5 + short-query fallback' if sessions.database.fts else 'substring search',
                  'sessions':len(sessions.list())}
        try:
            config = load_config()
            from dataclasses import asdict
            report["run_limits"] = asdict(config.run_limits)
            report["web"] = {"enabled":config.web.get("enabled",True),"search_provider":config.web.get("search_provider","bing")}
            report['configuration'] = 'valid'
            report['providers'] = [{'name':p.name,'model':p.model,'protocol':p.protocol,
                                    'api_key_present':bool(p.resolve_api_key())} for p in config.providers]
        except ConfigError:
            report['configuration'] = 'missing or invalid; configure .nonocode/config.yaml'
        print(json.dumps(report,ensure_ascii=False,indent=2))
