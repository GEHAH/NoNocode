"""Immutable content objects and task-linked deliverables/source evidence."""
from __future__ import annotations
import base64
import difflib
import hashlib
import io
import json
import mimetypes
import os
import stat
from contextlib import ExitStack
from pathlib import Path
import uuid
import zipfile

from nonocode.storage.database import now

MAX_OBJECT = 10_000_000


class ArtifactExcluded(ValueError):
    """A successful write intentionally outside the automatic artifact scope."""


class ProductStore:
    def __init__(self,database):
        self.database=database
        self.objects=database.path.parent/'objects'

    def put(self,data: bytes):
        if len(data)>MAX_OBJECT:raise ValueError('产物超过 10 MB 上限')
        digest=hashlib.sha256(data).hexdigest()
        if self.objects.is_symlink():raise ValueError('拒绝符号链接对象目录')
        self.objects.mkdir(exist_ok=True,parents=True,mode=0o700)
        path=self.objects/digest
        if path.is_symlink():raise ValueError('拒绝符号链接对象文件')
        temporary=self.objects/('.'+uuid.uuid4().hex+'.tmp')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'wb') as output:output.write(data)
            try:os.link(temporary,path)
            except FileExistsError:
                if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                    raise ValueError('已有对象校验失败')
        finally:temporary.unlink(missing_ok=True)
        return digest

    def content(self,digest):
        import re
        if not re.fullmatch(r'[0-9a-f]{64}',digest):raise ValueError('无效对象标识')
        path=self.objects/digest
        if path.is_symlink() or path.stat().st_size>MAX_OBJECT:raise ValueError('对象不可读取')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('产物内容校验失败')
        return data

    def _run(self,run_id):
        record=self.database.get_run(run_id)
        if record is None:raise ValueError('任务不存在')
        return record

    def capture_file(self,run_id,path):
        root=Path(self.database.workspace_path)
        target=Path(path)
        if not target.is_absolute():target=root/target
        resolved=target.resolve()
        if not resolved.is_relative_to(root):raise ArtifactExcluded('只能关联当前工作区内的文件')
        relative=resolved.relative_to(root)
        if any(part.startswith('.') or part in {'node_modules','__pycache__'} for part in relative.parts):
            raise ArtifactExcluded('隐藏文件与内部数据不纳入自动产物')
        if not resolved.is_file():raise ValueError('产物不是文件')
        if os.open in os.supports_dir_fd:
            with ExitStack() as stack:
                directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
                stack.callback(os.close,directory)
                for part in relative.parts[:-1]:
                    directory=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                    stack.callback(os.close,directory)
                descriptor=os.open(relative.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
                stream=stack.enter_context(os.fdopen(descriptor,'rb'))
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('产物不是普通文件')
                data=stream.read(MAX_OBJECT+1)
        else:
            with resolved.open('rb') as stream:data=stream.read(MAX_OBJECT+1)
        return self.capture(run_id,str(relative),data,kind='file')

    def capture(self,run_id,relative_path,data,*,kind='file'):
        from pathlib import PurePosixPath
        path=PurePosixPath(relative_path.replace('\\','/'))
        if path.is_absolute() or '..' in path.parts or not path.name:raise ValueError('无效产物路径')
        self._run(run_id)
        sha=self.put(data)
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            last=connection.execute('SELECT * FROM artifacts WHERE workspace_id=? AND relative_path=? ORDER BY version DESC LIMIT 1',
                                    (self.database.workspace_id,str(path))).fetchone()
            if last and last['sha256']==sha:
                artifact_id=last['id']
            else:
                artifact_id=uuid.uuid4().hex
                connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)',
                    (artifact_id,self.database.workspace_id,str(path),sha,len(data),mimetypes.guess_type(str(path))[0] or 'application/octet-stream',kind,
                     last['version']+1 if last else 1,now()))
            connection.execute('INSERT OR IGNORE INTO artifact_runs VALUES(?,?)',(artifact_id,run_id))
        return self.artifact(artifact_id)

    def artifact(self,artifact_id):
        with self.database.connect() as connection:
            row=connection.execute('SELECT * FROM artifacts WHERE id=? AND workspace_id=?',(artifact_id,self.database.workspace_id)).fetchone()
        if not row:raise ValueError('产物不存在')
        return dict(row)

    def artifacts(self,query='',run_id=''):
        with self.database.connect() as connection:
            rows=connection.execute('''SELECT a.*,group_concat(DISTINCT ar.run_id) AS run_ids FROM artifacts a
                LEFT JOIN artifact_runs ar ON ar.artifact_id=a.id LEFT JOIN runs r ON r.id=ar.run_id
                WHERE a.workspace_id=? AND instr(lower(a.relative_path),lower(?))>0
                AND (?='' OR ar.run_id=? OR r.root_run_id=?) GROUP BY a.id ORDER BY a.created_at DESC,a.version DESC LIMIT 100''',
                (self.database.workspace_id,query,run_id,run_id,run_id)).fetchall()
        return [dict(row) for row in rows]

    def preview(self,artifact_id):
        item=self.artifact(artifact_id);data=self.content(item['sha256'])
        try:
            text=data.decode('utf-8')
            if '\0' in text:raise UnicodeError()
        except UnicodeError:return {**item,'text':'此文件为二进制内容，请下载查看','binary':True,'truncated':False}
        return {**item,'text':text[:30000],'binary':False,'truncated':len(text)>30000}

    def diff(self,artifact_id,other_id):
        a,b=self.artifact(artifact_id),self.artifact(other_id)
        if a['relative_path']!=b['relative_path']:raise ValueError('请选择同一产物的两个版本')
        before,after=self.content(a['sha256']).decode('utf-8'),self.content(b['sha256']).decode('utf-8')
        return {'text':''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile=f"v{a['version']}",tofile=f"v{b['version']}"))[:100000]}

    def record_source(self,run_id,item,kind):
        from nonocode.tools.web import public_url
        self._run(run_id)
        url=str(public_url(item.get('url','')))
        text=str(item.get('text',item.get('snippet','')))[:50000]
        sha=self.put(text.encode())
        with self.database.connect() as connection:
            existing=connection.execute('SELECT id FROM sources WHERE workspace_id=? AND run_id=? AND url=? AND sha256=? AND kind=?',
                (self.database.workspace_id,run_id,url,sha,kind)).fetchone()
            if existing:return existing['id']
            source_id=uuid.uuid4().hex
            connection.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)',
                (source_id,self.database.workspace_id,run_id,url,str(item.get('title',''))[:1000],text[:2000],sha,item.get('retrieved_at') or now(),kind,int(bool(item.get('truncated')))))
        return source_id

    def sources(self,query='',run_id=''):
        with self.database.connect() as connection:
            rows=connection.execute('''SELECT s.* FROM sources s JOIN runs r ON r.id=s.run_id
                WHERE s.workspace_id=? AND instr(lower(s.title||s.url),lower(?))>0 AND (?='' OR s.run_id=? OR r.root_run_id=?)
                ORDER BY s.retrieved_at DESC LIMIT 100''',(self.database.workspace_id,query,run_id,run_id,run_id)).fetchall()
        return [dict(row) for row in rows]

    def source(self,source_id):
        with self.database.connect() as connection:
            row=connection.execute('SELECT * FROM sources WHERE workspace_id=? AND id=?',(self.database.workspace_id,source_id)).fetchone()
        if not row:raise ValueError('来源不存在')
        item=dict(row);item['text']=self.content(item['sha256']).decode('utf-8');return item

    def download(self,artifact_id,*,bundle=False):
        item=self.artifact(artifact_id);data=self.content(item['sha256']);filename=Path(item['relative_path']).name
        if bundle:
            with self.database.connect() as connection:
                ids=[row[0] for row in connection.execute('SELECT run_id FROM artifact_runs WHERE artifact_id=?',(artifact_id,))]
            root_ids={self._run(run_id).get('root_run_id') or run_id for run_id in ids}
            sources={source['id']:self.source(source['id']) for run_id in root_ids for source in self.sources(run_id=run_id)}
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('deliverable/'+filename,data)
                archive.writestr('sources.json',json.dumps(list(sources.values()),ensure_ascii=False,indent=2))
                archive.writestr('manifest.json',json.dumps(item,ensure_ascii=False,indent=2))
            data=output.getvalue();filename=filename+'.zip'
        return {'filename':filename,'base64':base64.b64encode(data).decode(),'media_type':'application/zip' if bundle else item['media_type']}

    def record_tool(self,run_id,tool_name,arguments,result):
        if result.is_error:return
        if tool_name in {'WriteFile','EditFile'}:
            try:self.capture_file(run_id,arguments.get('file_path',''))
            except ArtifactExcluded:return
        elif tool_name in {'WebSearch','WebFetch'}:
            data=json.loads(result.output)
            for item in data.get('results',[]) if tool_name=='WebSearch' else [data]:
                self.record_source(run_id,item,'search' if tool_name=='WebSearch' else 'page')
