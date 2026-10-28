"""Product services shared by terminal commands, offline CLI and browser panels."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import uuid

from nonocode.memory.auto_memory import ENTRYPOINT_NAME, MemoryManager, parse_frontmatter
from nonocode.memory.session import SessionManager

_NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
_HEADER = re.compile(r'\A---\s*\n.*?\n---[^\S\n]*\n', re.S)


class ProductService:
    def __init__(self, sessions: SessionManager, memories: MemoryManager) -> None:
        self.sessions = sessions
        self.memories = memories
        self.database = sessions.database

    def history(self, query: str = '') -> list[dict]:
        if query.strip():
            return self.sessions.search(query)
        return [{'session_id': meta.id, 'title': meta.title, 'last_active': meta.last_active.isoformat(),
                 'snippet': meta.summary or f'{meta.message_count} 条消息'} for meta in self.sessions.list()[:30]]

    def _directory(self, scope: str) -> Path:
        if scope not in {'user','project'}:
            raise ValueError('记忆范围须为 user 或 project')
        return self.memories.user_mem_dir if scope == 'user' else self.memories.project_mem_dir

    def _path(self, scope: str, name: str) -> Path:
        if len(name) > 100 or not _NAME.fullmatch(name):
            raise ValueError('记忆名称须为小写英文、数字和短横线')
        directory = self._directory(scope)
        path = directory / (name + '.md')
        if path.is_symlink() or (directory / ENTRYPOINT_NAME).is_symlink():
            raise ValueError('拒绝操作符号链接记忆或索引')
        return path

    def memory_entries(self, query: str = '') -> list[dict]:
        entries = []
        for scope in ('user','project'):
            directory = self._directory(scope)
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob('*.md')):
                if path.name == ENTRYPOINT_NAME or path.is_symlink() or not path.is_file():
                    continue
                if not _NAME.fullmatch(path.stem):
                    continue
                text = path.read_text(encoding='utf-8')
                metadata = parse_frontmatter(text)
                body = _HEADER.sub('',text,count=1).strip()
                entries.append({'scope': scope,'name': path.stem,'type': metadata.type or 'reference',
                                'description':metadata.description,'body':body,'path':str(path),
                                'updated_at':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()})
        self.database.index_memories(entries)
        return self.database.search_memories(query)

    def memory(self, scope: str, name: str) -> dict:
        self._path(scope,name)
        return next((item for item in self.memory_entries() if item['scope']==scope and item['name']==name), None) or self._missing()

    @staticmethod
    def _missing():
        raise ValueError('记忆不存在')

    def update_memory(self, scope: str, name: str, body: str, *, create: bool = False) -> dict:
        if not body.strip() or len(body) > 100_000:
            raise ValueError('记忆内容须为 1–100000 个字符')
        path = self._path(scope,name)
        if path.exists() and create:
            raise ValueError('同名记忆已存在，请编辑已有记忆')
        if not path.exists() and not create:
            raise ValueError('记忆不存在')
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            original = path.read_text(encoding='utf-8')
            header = _HEADER.match(original)
            prefix = header.group(0) if header else ''
            description = parse_frontmatter(original).description
        else:
            description = name
            kind = 'user' if scope == 'user' else 'project'
            prefix = f'---\nname: {name}\ndescription: {name}\nmetadata:\n  type: {kind}\nsource: manual\n---\n'
        temporary = path.parent / f'.{uuid.uuid4().hex}.tmp'
        try:
            temporary.write_text(prefix+'\n'+body.strip()+'\n',encoding='utf-8')
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)
        self._update_index(scope,name,description)
        return self.memory(scope,name)

    def forget_memory(self, scope: str, name: str) -> None:
        path = self._path(scope,name)
        if not path.is_file():
            raise ValueError('记忆不存在')
        path.unlink()
        self._update_index(scope,name,None)
        self.memory_entries()

    def _update_index(self, scope: str, name: str, description: str | None) -> None:
        path = self._directory(scope) / ENTRYPOINT_NAME
        if path.is_symlink():
            raise ValueError('拒绝操作符号链接索引')
        existing = path.read_text(encoding='utf-8') if path.exists() else ''
        link = re.compile(r'\]\(\./?' + re.escape(name) + r'\.md\)|\]\(' + re.escape(name) + r'\.md\)')
        lines = [line for line in existing.splitlines() if not link.search(line)]
        if description is not None:
            lines.append(f'- [{name}]({name}.md) — {description}')
        path.write_text('\n'.join(lines).rstrip()+'\n',encoding='utf-8')
