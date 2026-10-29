"""Portable local recovery bundles with checksums and isolated restore."""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import zipfile

import yaml

from nonocode.storage.database import now

MAX_BUNDLE_BYTES = 100_000_000


def _config_without_keys(raw):
    if isinstance(raw, dict):
        return {key: ('' if str(key).lower().replace('-', '_') in {'api_key','key','token','access_token','refresh_token','secret','password','authorization','x_subscription_token','x_api_key'}
                      else _config_without_keys(value)) for key,value in raw.items()}
    if isinstance(raw, list):
        return [_config_without_keys(value) for value in raw]
    return raw


def backup_bundle(database, destination: Path, *, user_dir: Path | None = None) -> Path:
    destination = destination.expanduser().absolute()
    if destination.exists():
        raise ValueError('备份目标必须是尚不存在的新文件')
    workspace = Path(database.workspace_path)
    if database.path.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("数据库超过 100 MB 备份上限")
    user_dir = user_dir or Path.home()/'.nonocode'
    files = {}
    for scope,base in [('project',workspace/'.nonocode'),('user',user_dir)]:
        if base.is_symlink():
            raise ValueError('备份不支持符号链接配置目录')
        for name in ['memory','evolution','sessions','permissions.yaml','permissions.local.yaml','config.yaml','config.local.yaml','workspace-id']:
            source = base/name
            if source.is_symlink():
                raise ValueError(f'备份不支持符号链接: {name}')
            candidates = sorted(source.rglob('*')) if source.is_dir() else ([source] if source.is_file() else [])
            for path in candidates:
                if path.is_symlink():
                    raise ValueError(f'备份不支持符号链接: {path.name}')
                if not path.is_file():
                    continue
                if path.stat().st_size > MAX_BUNDLE_BYTES:
                    raise ValueError('单个备份文件超过 100 MB 上限')
                payload = path.read_bytes()
                if path.name in {'config.yaml','config.local.yaml'}:
                    payload = yaml.safe_dump(_config_without_keys(yaml.safe_load(payload)),allow_unicode=True).encode()
                if len(files) >= 4999 or sum(map(len,files.values())) + len(payload) > MAX_BUNDLE_BYTES:
                    raise ValueError("备份超过 100 MB 或 5000 个文件上限")
                files[f'{scope}/.nonocode/{path.relative_to(base).as_posix()}'] = payload
    instructions=workspace/'NONOCODE.md'
    if instructions.is_file() and not instructions.is_symlink():
        files['project/NONOCODE.md']=instructions.read_bytes()
    with tempfile.TemporaryDirectory(prefix='nonocode-backup-') as temporary:
        copy=database.backup(Path(temporary)/'nonocode.db')
        # This is a project recovery bundle; remove unrelated workspace records.
        with closing(sqlite3.connect(copy)) as connection, connection:
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute('DELETE FROM run_steps WHERE run_id IN (SELECT id FROM runs WHERE workspace_id != ?)',(database.workspace_id,))
            connection.execute('DELETE FROM artifact_runs WHERE run_id IN (SELECT id FROM runs WHERE workspace_id != ?)',(database.workspace_id,))
            for table in ['sources','usage_events','artifacts','memory_evidence','memory_usage','settings_audit','workspace_aliases',
                          'session_records','sessions','runs','memory_index']:
                connection.execute(f'DELETE FROM {table} WHERE workspace_id != ?',(database.workspace_id,))
            connection.execute('DELETE FROM workspaces WHERE id != ?',(database.workspace_id,))
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='transcript_fts'").fetchone():
                connection.execute("INSERT INTO transcript_fts(transcript_fts) VALUES('rebuild')")
            connection.commit()
            connection.execute('VACUUM')
            hashes={row[0] for row in connection.execute('SELECT sha256 FROM artifacts UNION SELECT sha256 FROM sources')}
        from nonocode.storage.products import ProductStore
        for digest in hashes:
            files['database/objects/'+digest]=ProductStore(database).content(digest)
        files['database/nonocode.db']=copy.read_bytes()
    if len(files)>5000 or sum(map(len,files.values()))>MAX_BUNDLE_BYTES:
        raise ValueError('备份超过 100 MB 或 5000 个文件上限')
    manifest={'format':1,'created_at':now(),'workspace_id':database.workspace_id,
              'config_keys_redacted':True,'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as output, zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,data in files.items():
                archive.writestr(name,data)
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def restore_bundle(source: Path, destination: Path) -> Path:
    destination=destination.expanduser().absolute()
    if destination.exists():
        raise ValueError('恢复目标必须是尚不存在的新目录，避免覆盖现有数据')
    with zipfile.ZipFile(source) as archive:
        infos=archive.infolist()
        if len(infos)>5001 or sum(info.file_size for info in infos)>MAX_BUNDLE_BYTES+1_000_000:
            raise ValueError('备份文件超过恢复上限')
        names=[info.filename for info in infos]
        if len(set(names))!=len(names) or 'manifest.json' not in names:
            raise ValueError('备份清单缺失或含重复文件')
        for info in infos:
            path=PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('备份包含不安全路径')
            if info.filename!='manifest.json' and (not path.parts or path.parts[0] not in {'project','user','database'}):
                raise ValueError('备份包含未知文件')
        manifest=json.loads(archive.read('manifest.json'))
        if (not isinstance(manifest,dict) or manifest.get('format')!=1
                or not isinstance(manifest.get('files'),dict)
                or set(manifest['files'])!=set(names)-{'manifest.json'}):
            raise ValueError('备份格式或文件清单不匹配')
        files={name:archive.read(name) for name in manifest['files']}
        if any(hashlib.sha256(data).hexdigest()!=manifest['files'][name] for name,data in files.items()):
            raise ValueError('备份校验失败，文件可能损坏')
        if 'database/nonocode.db' not in files:
            raise ValueError('备份缺少数据库')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.nonocode-restore-',dir=destination.parent) as temporary:
        staged=Path(temporary)/'restored';staged.mkdir(mode=0o700)
        for name,data in files.items():
            path=staged/name;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            path.write_bytes(data);path.chmod(0o600)
        (staged/'project').mkdir(exist_ok=True)
        new_path=str((destination/'project').resolve())
        new_id=hashlib.sha256(new_path.encode()).hexdigest()
        with closing(sqlite3.connect(staged/'database/nonocode.db')) as connection, connection:
            if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('备份数据库完整性检查失败')
            old_id=manifest['workspace_id']
            if connection.execute('SELECT count(*) FROM workspaces WHERE id=?',(old_id,)).fetchone()[0]!=1:
                raise ValueError('备份工作区不匹配')
            connection.execute('UPDATE workspaces SET id=?,path=? WHERE id=?',(new_id,new_path,old_id))
            tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ['sessions','session_records','runs','memory_index','artifacts','sources','usage_events','memory_evidence','memory_usage','settings_audit','workspace_aliases']:
                if table not in tables:continue
                connection.execute(f'UPDATE {table} SET workspace_id=? WHERE workspace_id=?',(new_id,old_id))
            if 'usage_events' in tables:
                connection.execute("UPDATE usage_events SET status='interrupted',finished_at=? WHERE status='running'",(now(),))
            connection.execute('DELETE FROM memory_index') # Markdown will rebuild paths.
            connection.execute("UPDATE runs SET status='interrupted',error='从备份恢复；不会重放工具',owner_pid=0,finished_at=?,updated_at=? WHERE status IN ('running','waiting_permission')",(now(),now()))
            connection.execute("UPDATE run_steps SET status='interrupted',finished_at=? WHERE status IN ('running','waiting_permission')",(now(),))
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('备份数据库关联校验失败')
        identity=staged/'project/.nonocode/workspace-id'
        identity.parent.mkdir(parents=True,exist_ok=True)
        identity.write_text(new_id+'\n');identity.chmod(0o600)
        instructions='''恢复已完成。project/ 是恢复工作区，user/ 是隔离用户目录，database/ 是数据库。
配置中的密钥字段已清空，请通过环境变量重新提供 API 密钥。
不会启动模型、重放工具或覆盖现有工作区。项目源代码和未归档的外部文件不在此备份中。归档产物及来源快照已恢复。
运行时请在 shell 中执行（替换为本目录的绝对路径）：
  cd /absolute/restore/project
  HOME=/absolute/restore/user NONOCODE_DATA_DIR=/absolute/restore/database nonocode
历史文件中的旧绝对路径作为原始证据保留；需要原项目文件时请自行复制。
'''
        (staged/'RESTORE.txt').write_text(instructions)
        if destination.exists():
            raise ValueError('恢复目标已存在')
        staged.rename(destination)
    return destination
