import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest
import yaml

from nonocode.config import load_config
from nonocode.conversation import Message
from nonocode.memory.session import SessionManager
from nonocode.storage.database import Database
from nonocode.storage.recovery import backup_bundle, restore_bundle


def setup(tmp_path):
    workspace=tmp_path/'original';workspace.mkdir()
    profile=tmp_path/'profile';(profile/'memory').mkdir(parents=True)
    (profile/'memory/pref.md').write_text('Use Chinese')
    internal=workspace/'.nonocode'
    (internal/'memory').mkdir(parents=True)
    (internal/'memory/project.md').write_text('Project details')
    (internal/'evolution').mkdir()
    (internal/'evolution/candidates.json').write_text('{"candidate":"verified"}')
    (internal/'config.yaml').write_text('providers:\n- name: test\n  protocol: anthropic\n  model: test\n  base_url: https://example.com\n  api_key: secret-value\n  max_output_tokens: 4096\nrun_limits:\n  max_tokens: 12345\nweb:\n  search_api_key_env: SEARCH_KEY\n')
    manager=SessionManager(str(workspace));session=manager.create()
    session.append(Message(role='user',content='Saved question'))
    session.append(Message(role='assistant',content='Saved response'))
    manager.database.create_run('recovery-task',session.session_id,'go','test')
    manager.database.start_step('recovery-task','step','Bash',{'command':'printf done'})
    session.close()
    return workspace,profile,manager,session.session_id


def test_backup_restore_isolated_workspace_and_database_integrity(tmp_path):
    workspace,profile,manager,session_id=setup(tmp_path)
    archive=backup_bundle(manager.database,tmp_path/'backup.zip',user_dir=profile)
    assert archive.stat().st_mode & 0o777 == 0o600
    restored=restore_bundle(archive,tmp_path/'restored')
    assert (restored/'project/.nonocode/memory/project.md').read_text()=='Project details'
    assert (restored/'user/.nonocode/memory/pref.md').read_text()=='Use Chinese'
    config=load_config(restored/'project/.nonocode/config.yaml')
    assert config.providers[0].api_key==''
    assert config.providers[0].max_output_tokens==4096
    assert config.run_limits.max_tokens==12345 and config.web['search_api_key_env']=='SEARCH_KEY'
    database=Database(str(restored/'project'),path=restored/'database/nonocode.db')
    assert database.get_run('recovery-task')['status']=='interrupted'
    assert database.get_run('recovery-task')['steps'][0]['status']=='interrupted'
    resume=SessionManager(str(restored/'project'),database=database).resume(session_id)
    try:
        assert [message.content for message in resume.messages]==['Saved question','Saved response']
    finally:resume.session.close()
    assert (restored/'RESTORE.txt').exists()
    assert (workspace/'.nonocode/config.yaml').read_text().find('secret-value')>=0
    with pytest.raises(ValueError):restore_bundle(archive,restored)
    with pytest.raises(ValueError):backup_bundle(manager.database,archive,user_dir=profile)


@pytest.mark.parametrize('attack',['traversal','checksum','symlink','duplicate'])
def test_restore_rejects_unsafe_or_damaged_archives(tmp_path,attack):
    workspace,profile,manager,_=setup(tmp_path)
    original=backup_bundle(manager.database,tmp_path/'original.zip',user_dir=profile)
    bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(original) as source,zipfile.ZipFile(bad,'w') as target:
        for info in source.infolist():
            data=source.read(info.filename)
            if attack=='checksum' and info.filename=='project/.nonocode/memory/project.md':data=b'changed'
            target.writestr(info,data)
        if attack=='traversal':target.writestr('../escape.txt','bad')
        if attack=='symlink':
            info=zipfile.ZipInfo('project/evil');info.external_attr=(0o120777 << 16);target.writestr(info,'/etc/passwd')
        if attack=='duplicate':
            with pytest.warns(UserWarning):target.writestr('manifest.json','{}')
    with pytest.raises(ValueError):restore_bundle(bad,tmp_path/'target')
    assert not (tmp_path/'target').exists() and not (tmp_path/'escape.txt').exists()


def test_backup_does_not_include_other_workspaces(tmp_path):
    workspace,profile,manager,_=setup(tmp_path)
    other=Database(str(tmp_path/'other'),path=manager.database.path)
    other.create_run('unrelated','other-session','Private other project','test')
    archive=backup_bundle(manager.database,tmp_path/'bundle.zip',user_dir=profile)
    restored=restore_bundle(archive,tmp_path/'restored')
    assert b'Private other project' not in (restored/'database/nonocode.db').read_bytes()
    with sqlite3.connect(restored/'database/nonocode.db') as connection:
        assert connection.execute('SELECT count(*) FROM workspaces').fetchone()[0]==1
        assert connection.execute('SELECT count(*) FROM runs WHERE id="unrelated"').fetchone()[0]==0
