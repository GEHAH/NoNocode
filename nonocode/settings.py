"""Validated local overrides; responses and audit records never contain keys."""
from __future__ import annotations
import asyncio
from dataclasses import asdict,replace
import hashlib
import json
import os
from pathlib import Path
import uuid

import yaml

from nonocode.config import _load_single_file,_merge_config,AppConfig
from nonocode.storage.database import now
from nonocode.validator import validate_config_structure

KEEP='__KEEP__'
EDITABLE={'providers','permission_mode','run_limits','web'}


class SettingsService:
    def __init__(self,work_dir,database=None):
        self.root=Path(work_dir);self.database=database
        self.paths=[Path.home()/'.nonocode/config.yaml',self.root/'.nonocode/config.yaml',self.root/'.nonocode/config.local.yaml']

    def revision(self):
        digest=hashlib.sha256()
        for path in self.paths:
            if path.is_symlink():raise ValueError('配置不支持符号链接')
            digest.update(str(path).encode());digest.update(path.read_bytes() if path.exists() else b'')
        return digest.hexdigest()

    def config(self):
        result=None
        for path in self.paths:
            if path.exists():
                item=_load_single_file(path,allow_partial=result is not None)
                result=item if result is None else _merge_config(result,item)
        return result or AppConfig([])

    def editable(self,config=None,*,redact=True):
        config=config or self.config()
        providers=[]
        for provider in config.providers:
            values={key:value for key,value in asdict(provider).items() if not key.startswith('_')}
            if redact:
                values['api_key']=KEEP
                values['api_key_present']=bool(provider.resolve_api_key())
            providers.append(values)
        return {'providers':providers,'permission_mode':config.permission_mode,'run_limits':asdict(config.run_limits),
                'web':{'enabled':True,'search_provider':'bing',**config.web}}

    def read(self):
        fields={}
        layers=[]
        for path in self.paths:
            if path.exists():
                raw=yaml.safe_load(path.read_text()) or {}
                layers.append({'path':str(path),'fields':sorted(key for key in raw if key in EDITABLE)})
                for key in raw:
                    if key in EDITABLE:
                        fields[key]=str(path)
                        if isinstance(raw[key],dict):
                            for nested in raw[key]:fields[key+"."+nested]=str(path)
        return {'revision':self.revision(),'values':self.editable(),'layers':layers,'field_sources':fields,
                'note':'__KEEP__ 保留已有密钥；空字符串改为从环境变量读取。金额按手工配置价格估算。'}

    def validate(self,values):
        if not isinstance(values,dict) or set(values)-EDITABLE:raise ValueError('包含不支持的配置项')
        existing=self.config();merged=self.editable(existing,redact=False)
        for key,value in values.items():merged[key]=value
        old={provider.name:provider for provider in existing.providers}
        providers=merged.get('providers')
        if not isinstance(providers,list):raise ValueError('providers 须为列表')
        for index,entry in enumerate(providers):
            if not isinstance(entry,dict):raise ValueError('provider 须为对象')
            entry=dict(entry);entry.pop('api_key_present',None)
            if entry.get('api_key',KEEP)==KEEP:
                previous=old.get(entry.get('name'))
                if previous is None and index<len(existing.providers):previous=existing.providers[index]
                entry['api_key']=previous.api_key if previous else '' 
            providers[index]=entry
        validated=validate_config_structure(merged)
        for key in ("providers","run_limits","web","permission_mode"):merged[key]=validated[key]
        return merged,validated

    def save(self,values,revision):
        import fcntl
        directory=self.root/'.nonocode'
        if directory.is_symlink():raise ValueError('拒绝符号链接配置目录')
        directory.mkdir(parents=True,exist_ok=True)
        lock=directory/'settings.lock'
        if lock.is_symlink():raise ValueError('拒绝符号链接配置锁')
        with lock.open('a') as handle:
            os.chmod(lock,0o600)
            fcntl.flock(handle,fcntl.LOCK_EX)
            if revision!=self.revision():raise ValueError('配置已被其他窗口修改，请刷新后重试')
            merged,_=self.validate(values)
            target=self.paths[-1]
            original=yaml.safe_load(target.read_text()) if target.exists() else {}
            document=dict(original or {});document.update(merged)
            temporary=directory/('.'+uuid.uuid4().hex+'.tmp')
            try:
                fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as output:yaml.safe_dump(document,output,allow_unicode=True,sort_keys=False)
                os.replace(temporary,target)
            finally:temporary.unlink(missing_ok=True)
            result=self.read()
            if self.database:
                with self.database.connect() as connection:
                    connection.execute('INSERT INTO settings_audit VALUES(?,?,?,?,?)',
                        (uuid.uuid4().hex,self.database.workspace_id,result['revision'],json.dumps(sorted(values)),now()))
            return result

    async def test(self,values,*,session_id=''):
        merged,_=self.validate(values)
        from nonocode.config import ProviderConfig
        from nonocode.client import create_client
        from nonocode.conversation import ConversationManager
        from nonocode.tools.base import StreamEnd,TextDelta
        from nonocode.execution import ExecutionScope,current_execution,phase
        provider=ProviderConfig(**merged['providers'][0])
        if not provider.resolve_api_key():raise ValueError('当前 provider 没有可用密钥')
        provider=replace(provider,thinking=False,max_output_tokens=128)
        run_id=uuid.uuid4().hex;token=None;label=phase.set('connection_test')
        if self.database:
            self.database.create_run(run_id,session_id,'模型连接测试','diagnostic')
            with self.database.connect() as connection:connection.execute('UPDATE runs SET root_run_id=id WHERE id=?',(run_id,))
            from nonocode.limits import RunLimits
            import time
            token=current_execution.set(ExecutionScope(self.database,run_id,run_id,session_id,RunLimits(**merged['run_limits']),time.monotonic()))
        client=None;status='failed';seen=False
        try:
            client=create_client(provider)
            conversation=ConversationManager();conversation.add_user_message('Reply with exactly OK.')
            stream=client.stream(conversation)
            try:
                async with asyncio.timeout(20):
                    async for event in stream:
                        if isinstance(event,TextDelta) and event.text.strip():seen=True
                        if isinstance(event,StreamEnd):status='completed'
            finally:
                close=getattr(stream,'aclose',None)
                if close:await close()
            if status!='completed' or not seen:raise ValueError('模型未返回完整文本响应')
            return {'ok':True,'provider':provider.name,'model':provider.model,'message':'已收到完整模型响应'}
        except asyncio.CancelledError:
            status='cancelled';raise
        except Exception as exc:
            # SDK bodies may echo request details; return only the exception class.
            raise ValueError('连接测试失败：'+type(exc).__name__) from None
        finally:
            if self.database:self.database.update_run(run_id,status,error='' if status=='completed' else '连接测试未完成')
            if token is not None:current_execution.reset(token)
            phase.reset(label)
            sdk=getattr(client,'_client',None)
            if sdk is not None:
                try:await sdk.close()
                except Exception:pass
