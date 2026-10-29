"""Human-readable provenance and exact index/body access receipts."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import yaml

from nonocode.storage.database import now

HEADER=re.compile(r'\A---\s*\n(.*?)\n---[^\S\n]*\n',re.S)


def metadata(text):
    match=HEADER.match(text)
    if not match:return {}
    try:
        data=yaml.safe_load(match.group(1))
        return data if isinstance(data,dict) else {}
    except yaml.YAMLError:return {}


def active(text):
    values=metadata(text)
    if values.get('enabled') is False:return False
    expiry=values.get('expires_at')
    if expiry:
        try:
            value=datetime.fromisoformat(str(expiry).replace('Z','+00:00'))
            if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
            if value<=datetime.now(timezone.utc):return False
        except ValueError:return False
    return True


def filter_index(text,directory):
    lines=[]
    for line in text.splitlines():
        names=re.findall(r'\]\((?:\./)?([a-z0-9-]+\.md)\)',line)
        include=True
        for name in names:
            path=Path(directory)/name
            try:
                if path.is_file() and (path.is_symlink() or not active(path.read_text())):include=False
            except OSError:include=False
        if include:lines.append(line)
    return '\n'.join(lines)


def evidence(database,scope,name,text,*,source='',quote='',session_id=''):
    values=metadata(text);digest=hashlib.sha256(text.encode()).hexdigest()
    item={'scope':scope,'name':name,'sha256':digest,'source':source or values.get('source','legacy'),
          'evidence':quote or str(values.get('evidence','')),'session_id':session_id or str(values.get('session_id','')),
          'expires_at':str(values.get('expires_at','')),'verified_at':str(values.get('verified_at','')),'updated_at':now()}
    with database.connect() as connection:
        connection.execute('''INSERT INTO memory_evidence VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,scope,name) DO UPDATE SET
            sha256=excluded.sha256,source=excluded.source,evidence=excluded.evidence,session_id=excluded.session_id,expires_at=excluded.expires_at,
            verified_at=excluded.verified_at,updated_at=excluded.updated_at''',
            (database.workspace_id,scope,name,digest,item['source'],item['evidence'],item['session_id'],item['expires_at'],item['verified_at'],item['updated_at']))
    return {**item,'enabled':values.get('enabled',True),'active':active(text),'topic':str(values.get('topic',''))}


def record_index(database,run_id,directories):
    for scope,directory in directories:
        directory=Path(directory)
        index=directory/'MEMORY.md'
        if not index.is_file() or index.is_symlink():continue
        text=filter_index(index.read_text(),directory)
        for name in set(re.findall(r'\]\((?:\./)?([a-z0-9-]+)\.md\)',text)):
            path=directory/(name+'.md')
            if not path.is_file() or path.is_symlink():continue
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            with database.connect() as connection:
                connection.execute('INSERT INTO memory_usage(workspace_id,run_id,scope,name,kind,sha256,created_at) VALUES(?,?,?,?,?,?,?)',
                                   (database.workspace_id,run_id,scope,name,'index',digest,now()))


def record_read(database,run_id,path):
    target=Path(path).resolve()
    for scope,directory in [('project',Path(database.workspace_path)/'.nonocode/memory'),('user',Path.home()/'.nonocode/memory')]:
        if target.parent==directory.resolve() and target.suffix=='.md' and target.name!='MEMORY.md':
            text=target.read_text()
            receipt=evidence(database,scope,target.stem,text)
            with database.connect() as connection:
                connection.execute('INSERT INTO memory_usage(workspace_id,run_id,scope,name,kind,sha256,created_at) VALUES(?,?,?,?,?,?,?)',
                    (database.workspace_id,run_id,scope,target.stem,'body',receipt['sha256'],now()))


def usage(database,run_id=''):
    with database.connect() as connection:
        rows=connection.execute('''SELECT m.* FROM memory_usage m LEFT JOIN runs r ON r.id=m.run_id WHERE m.workspace_id=? AND (?='' OR m.run_id=? OR r.root_run_id=?) ORDER BY m.created_at DESC LIMIT 200''',
                                (database.workspace_id,run_id,run_id,run_id)).fetchall()
    return [dict(row) for row in rows]
