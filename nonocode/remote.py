"""
Remote Control 服务器：通过 WebSocket 桥接 Agent 事件和 Web UI。

使用 websockets 库提供 HTTP（静态 HTML）+ WebSocket 服务，
让用户在浏览器中与 NoNocode Agent 交互。
"""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

import websockets
from websockets.asyncio.server import Server as WSServer, ServerConnection
from websockets.http11 import Request, Response

from nonocode.storage.runs import TrackedRun
from nonocode.agent import (
    Agent,
    CompactNotification,
    ErrorEvent,
    HookEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    RetryEvent,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from nonocode.client import create_client, resolve_context_window
from nonocode.commands import CommandContext, CommandRegistry, CommandType
from nonocode.commands.handlers import register_all_commands
from nonocode.commands.parser import parse_command
from nonocode.config import MCPServerConfig, ProviderConfig
from nonocode.conversation import ConversationManager, Message
from nonocode.commands.session_state import reset_agent_session
from nonocode.hooks import HookEngine
from nonocode.mcp import MCPManager
from nonocode.memory import MemoryManager, load_instructions
from nonocode.memory.session import Session, SessionManager
from nonocode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from nonocode.skills.loader import SkillLoader
from nonocode.tools import ToolRegistry, create_default_registry
from nonocode.tools.impl.tool_search import ToolSearchTool
from nonocode.tools.load_skill import LoadSkill
from nonocode.web_content import INDEX_HTML

log = logging.getLogger(__name__)


class RemoteServer:
    """Remote Control 核心：桥接 Agent 事件和 WebSocket 客户端。"""

    def __init__(
        self,
        providers: list[ProviderConfig],
        mcp_servers: list[MCPServerConfig] | None = None,
        hook_engine: HookEngine | None = None,
        addr: str = "127.0.0.1",
        port: int = 18888,
        config: object | None = None,
    ) -> None:
        self._config = config
        self.providers = providers
        self._mcp_server_configs = mcp_servers or []
        self.hook_engine = hook_engine
        self.addr = addr
        self.port = port

        # WebSocket 连接池（支持多客户端广播）
        self._connections: set[ServerConnection] = set()
        self._broadcast_lock = asyncio.Lock()
        self._replay_base = None
        self._event_log = deque()
        self._event_log_bytes = 0
        self._replay_truncated = False
        self._pending_perm_details = {}
        self._lab_task = None
        self._lab_job = {}

        # Agent 相关状态
        self.agent: Agent | None = None
        self.conversation: ConversationManager | None = None
        self.registry: ToolRegistry | None = None
        self.session_id: str = ""
        self._streaming = False
        self._current_run_id: str | None = None
        self._session_token_offset = 0
        self._cancel_event: asyncio.Event | None = None
        self._agent_task: asyncio.Task | None = None

        # 权限请求的 pending 队列：id -> Future
        self._pending_perms: dict[str, asyncio.Future[PermissionResponse]] = {}

        # 命令注册表
        self.command_registry = CommandRegistry()
        register_all_commands(self.command_registry)

        # MCP 相关
        self.mcp_manager: MCPManager | None = None
        self._mcp_instructions: str = ""
        self._mcp_server_info: str = ""

        # Skill 加载器
        self.skill_loader: SkillLoader | None = None

        # Memory / Session
        self.memory_manager: MemoryManager | None = None
        self.session_manager: SessionManager | None = None
        self.session: Session | None = None

    # ------------------------------------------------------------------
    # 启动入口
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """启动 HTTP + WebSocket 服务器。"""
        # 初始化 Agent
        self._init_agent()

        # 初始化 MCP（如果有配置）
        await self._init_mcp()

        print(f"\n  Remote UI: http://localhost:{self.port}\n")

        # websockets 的 serve 支持 process_request 回调来处理普通 HTTP
        async with websockets.serve(
            self._ws_handler,
            self.addr,
            self.port,
            process_request=self._process_http_request,
            max_size=4 * 1024 * 1024,  # 4MB 消息上限
        ):
            # 服务器启动后永久阻塞
            try:
                await asyncio.Future()
            finally:
                if self._lab_task and not self._lab_task.done():
                    self._lab_task.cancel()
                    await asyncio.gather(self._lab_task,return_exceptions=True)
                if self._agent_task is not None and self._agent_task is not asyncio.current_task():
                    self._agent_task.cancel()
                    await asyncio.gather(self._agent_task, return_exceptions=True)

    # ------------------------------------------------------------------
    # HTTP 请求处理（为 / 路径提供前端 HTML）
    # ------------------------------------------------------------------

    def _process_http_request(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        """拦截 HTTP 请求，对 / 路径返回 HTML 页面。
        返回 None 表示继续走 WebSocket 升级流程。
        """
        if request.path == '/ws':
            origin = request.headers.get('Origin')
            host = request.headers.get('Host', '')
            if origin and (urlsplit(origin).scheme not in {'http','https'} or urlsplit(origin).netloc.lower() != host.lower()):
                return Response(403,'Forbidden',websockets.Headers(),b'Cross-origin WebSocket denied')
            if self.addr in {'127.0.0.1','localhost','::1'} and urlsplit('//'+host).hostname not in {'127.0.0.1','localhost','::1'}:
                return Response(403,'Forbidden',websockets.Headers(),b'Invalid local host')
        if request.path == "/":
            return Response(
                200,
                "OK",
                websockets.Headers({"Content-Type": "text/html; charset=utf-8"}),
                INDEX_HTML.encode("utf-8"),
            )
        if request.path != "/ws":
            return Response(404, "Not Found", websockets.Headers(), b"404 Not Found")
        # /ws 路径 → 继续 WebSocket 升级
        return None

    # ------------------------------------------------------------------
    # WebSocket 连接处理
    # ------------------------------------------------------------------

    async def _ws_handler(self, websocket: ServerConnection) -> None:
        """处理单个 WebSocket 连接的全生命周期。"""
        try:
            # A single lock orders snapshot delivery before live events.
            async with self._broadcast_lock, asyncio.timeout(10):
                await websocket.send(json.dumps({"type":"connected", "data":{
                    "session":self.session_id, "cwd":os.getcwd()}},ensure_ascii=False))
                await websocket.send(json.dumps({"type":"commands", "data":self._build_command_list()},ensure_ascii=False))
                await websocket.send(json.dumps({"type":"state_sync", "data":self._state_snapshot()},ensure_ascii=False))
                self._connections.add(websocket)

            # 消息循环
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if not isinstance(msg,dict):
                    continue
                msg_type = msg.get("type", "")
                data = msg.get("data", {})
                if not isinstance(data,dict):
                    continue
                if msg_type == 'product_request':
                    await self._handle_product_request(websocket,data)
                    continue

                if msg_type == "user_message":
                    content = data.get("content", "")
                    if not isinstance(content,str):
                        continue
                    content = content.strip()
                    if content:
                        # 在后台任务中处理，不阻塞 WebSocket 读循环
                        asyncio.create_task(self._handle_user_message(content))

                elif msg_type == "permission_response":
                    self._handle_permission_response(data)

                elif msg_type == "cancel":
                    if self._cancel_event is not None:
                        self._cancel_event.set()
                    if self._agent_task is not None:
                        self._agent_task.cancel()
                    if self._lab_task and not self._lab_task.done():
                        self._lab_task.cancel()

                elif msg_type == "ping":
                    # 应用层保活
                    await self._broadcast({"type": "pong", "data": None})

        except websockets.ConnectionClosed:
            pass
        finally:
            self._connections.discard(websocket)

    async def _handle_product_request(self, websocket: ServerConnection, data: dict) -> None:
        from nonocode.product import ProductService
        from nonocode.storage.database import now
        request_id = data.get('request_id','')
        response = {'request_id':request_id,'ok':False}
        try:
            product = ProductService(self.session_manager,self.memory_manager)
            action = data.get('action','')
            if not isinstance(action,str):
                raise ValueError('无效操作')
            if (self._streaming or (self._lab_task and not self._lab_task.done())) and action in {'backup','memory_save','memory_forget','session_resume','session_rename','settings_save','settings_test','evolution_create','evolution_transition','evolution_task_save','evolution_verify','evolution_restore'}:
                raise ValueError('请等待或取消当前任务后再修改')
            def string(key, default=''):
                value = data.get(key,default)
                if not isinstance(value,str):
                    raise ValueError('字段 '+key+' 必须是文本')
                return value
            if action in {'artifacts','artifact','artifact_diff','artifact_download','sources','source','usage','memory_usage',
                'settings','settings_save','settings_test','evolution','evolution_create','evolution_transition','evolution_versions',
                'evolution_restore','evolution_tasks','evolution_task_save','evolution_draft','evolution_verify','evolution_job','evolution_cancel','evolution_report','evolution_trial'}:
                result = await self._workbench_request(action,data)
            elif action == 'history':
                result = await asyncio.to_thread(product.history,string('query'))
            elif action == 'runs':
                result = await asyncio.to_thread(product.database.list_runs)
            elif action == 'run':
                result = await asyncio.to_thread(product.database.get_run,string('id'))
                if result is None:
                    raise ValueError('任务不存在')
            elif action == 'memories':
                result = await asyncio.to_thread(product.memory_entries,string('query'))
            elif action == 'memory_save':
                result = await asyncio.to_thread(product.update_memory,string('scope'),string('name'),string('body'),create=data.get('create') is True,evidence=string('evidence'),session_id=string('session_id'),expires_at=data.get('expires_at'),enabled=data.get('enabled'),topic=data.get('topic'))
            elif action == 'memory_forget':
                await asyncio.to_thread(product.forget_memory,string('scope'),string('name'))
                result = {'message':'记忆已删除'}
            elif action == 'session_resume':
                session_id = string('id')
                if not self.session_manager._valid_id(session_id) or self.session_manager.database.session_metadata(session_id) is None:
                    raise ValueError('会话不存在')
                await self._handle_slash_command('/session resume '+session_id)
                result = {'message':'会话已恢复；请输入下一步，不会自动重放工具'}
            elif action == 'session_rename':
                session_id,title = string('id'),string('title')
                await asyncio.to_thread(self.session_manager.rename,session_id,title)
                if self.session and self.session.session_id == session_id:
                    self.session.meta.title = title.strip()
                result = {'message':'会话已重命名'}
            elif action == 'session_export':
                session_id = string('id')
                result = {'id':session_id,'text':await asyncio.to_thread(self.session_manager.export,session_id)}
            elif action == 'run_cancel':
                if not self.cancel_run(string('id')):
                    raise ValueError('任务已结束或不属于当前实例')
                result = {'message':'已请求取消任务'}
            elif action == 'backup':
                from nonocode.storage.recovery import backup_bundle
                destination = product.database.path.parent / 'backups' / ('nonocode-'+now().replace(':','-')+'.zip')
                result = {'path':str(await asyncio.to_thread(backup_bundle,product.database,destination))}
            else:
                raise ValueError('未知操作')
            response.update(ok=True,result=result)
        except (ValueError,OSError) as exc:
            response['error'] = str(exc)
        except Exception:
            log.exception('Product request failed')
            response['error'] = '操作失败，请查看本机日志'
        await websocket.send(json.dumps({'type':'product_result','data':response},ensure_ascii=False))

    async def _workbench_request(self, action, data):
        from nonocode.storage.products import ProductStore
        from nonocode.settings import SettingsService
        from nonocode.evolution.lab import EvolutionLab
        from nonocode.execution import usage_summary
        from nonocode.memory.provenance import usage
        database=self.session_manager.database
        products=ProductStore(database)
        settings=SettingsService(database.workspace_path,database)
        lab=EvolutionLab(self.agent,database,self.session_id)
        def text(key,default=''):
            value=data.get(key,default)
            if not isinstance(value,str):raise ValueError(key+' 必须是文本')
            return value
        if action=='artifacts':return await asyncio.to_thread(products.artifacts,text('query'),text('run_id'))
        if action=='artifact':return await asyncio.to_thread(products.preview,text('id'))
        if action=='artifact_diff':return await asyncio.to_thread(products.diff,text('id'),text('other_id'))
        if action=='artifact_download':return await asyncio.to_thread(products.download,text('id'),bundle=data.get('bundle') is True)
        if action=='sources':return await asyncio.to_thread(products.sources,text('query'),text('run_id'))
        if action=='source':return await asyncio.to_thread(products.source,text('id'))
        if action=='usage':return await asyncio.to_thread(usage_summary,database,text('run_id'))
        if action=='memory_usage':return await asyncio.to_thread(usage,database,text('run_id'))
        if action=='settings':return await asyncio.to_thread(settings.read)
        if action=='settings_save':
            result=await asyncio.to_thread(settings.save,data.get('values'),text('revision'))
            self._apply_workbench_config(settings.config())
            result['message']='已保存并应用到后续任务；已有对话保留'
            return result
        if action=='settings_test':return await settings.test(data.get('values'),session_id=self.session_id)
        if action=='evolution':return await asyncio.to_thread(lab.list)
        if action=='evolution_create':return await asyncio.to_thread(lab.create,text('rule'),text('kind','capability'),text('cue'),text('evidence'),text('replaces_id'))
        if action=='evolution_transition':return await asyncio.to_thread(lab.transition,text('id'),text('transition'))
        if action=='evolution_versions':return await asyncio.to_thread(lab.versions,text('id'))
        if action=='evolution_restore':return await asyncio.to_thread(lab.restore,text('id'),text('version_id'))
        if action=='evolution_tasks':return await asyncio.to_thread(lab.tasks.list)
        if action=='evolution_task_save':return await asyncio.to_thread(lab.register_task,data.get('spec'))
        if action=='evolution_draft':return await asyncio.to_thread(lab.draft_from_run,text('run_id'))
        if action=='evolution_report':return await asyncio.to_thread(lab.report,text('id'))
        if action=='evolution_trial':return await asyncio.to_thread(lab.trial,text('id'),data.get('index'),text('artifact_path'))
        if action=='evolution_cancel':
            if self._lab_task and not self._lab_task.done():self._lab_task.cancel()
            return {'message':'已请求取消验证'}
        if action=='evolution_job':return {key:value for key,value in self._lab_job.items() if key!='task'}
        if action=='evolution_verify':
            ids=data.get('task_ids')
            if not isinstance(ids,list) or any(not isinstance(value,str) for value in ids):raise ValueError('请选择验证任务')
            candidate_id=text('id');lab.store.get(candidate_id)
            # Validate before spawning, so form errors are visible immediately.
            records=[lab.tasks.get(task_id) for task_id in ids]
            if not 1<=len(ids)<=3 or len(set(ids))!=len(ids):raise ValueError('请选择 1–3 个不同任务')
            if lab.store.get(candidate_id).get('verification_policy')=='holdout-v1' and {record['spec'].get('split','development') for record in records}!={'development','holdout'}:
                raise ValueError('请选择至少一个 development 和一个 holdout 任务')
            self._lab_job={'id':__import__('uuid').uuid4().hex,'status':'running','candidate_id':candidate_id,'progress':'准备验证','run_id':None}
            def progress(message):
                from nonocode.execution import current_execution
                scope=current_execution.get()
                self._lab_job.update(progress=message,run_id=scope.root_id if scope else None)
            async def execute():
                try:
                    report=await lab.verify(candidate_id,ids,progress)
                    self._lab_job.update(status='completed',outcome=report['outcome'],report_id=report['id'],run_id=report.get('tracking_run_id'),progress=report['reason'])
                except asyncio.CancelledError:
                    self._lab_job.update(status='cancelled',progress='已取消验证；旧的通过结果不会用于采纳')
                except Exception as exc:
                    log.exception('Evolution verification failed')
                    self._lab_job.update(status='failed',progress=str(exc)[:2000])
            self._lab_task=asyncio.create_task(execute())
            return dict(self._lab_job)
        raise ValueError('未知工作台操作')

    def _apply_workbench_config(self, config):
        if not config.providers:raise ValueError('至少需要一个模型')
        provider=config.providers[0]
        client=create_client(provider)
        self._config=config;self.providers=config.providers
        self.agent.client=client;self.agent.protocol=provider.protocol
        self.agent.context_window=provider.get_context_window();self.agent.run_limits=config.run_limits
        self.agent.permission_checker.mode=PermissionMode(config.permission_mode)
        self.agent.permission_mode=PermissionMode(config.permission_mode)
        from nonocode.tools.web import WebSearch,WebFetch
        for name,tool in [('WebSearch',WebSearch(config.web)),('WebFetch',WebFetch())]:
            self.registry.register(tool)
            if config.web.get('enabled',True):self.registry.enable(name)
            else:self.registry.disable(name)
        agent_tool=self.registry.get('Agent')
        if agent_tool is not None:agent_tool._provider_config=provider
        search=self.registry.get('ToolSearch')
        if search is not None:search.protocol=provider.protocol
        executor=getattr(self,'skill_executor',None)
        if executor is not None:executor.client=client;executor.protocol=provider.protocol

    # ------------------------------------------------------------------
    # Agent 初始化（复刻 TUI 的 _select_provider 流程）
    # ------------------------------------------------------------------

    def _init_agent(self) -> None:
        """初始化 Agent 及相关子系统。"""
        provider = self.providers[0]
        work_dir = os.getcwd()
        home = Path.home()

        # 权限系统
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(work_dir),
            rule_engine=RuleEngine(
                user_rules_path=home / ".nonocode" / "permissions.yaml",
                project_rules_path=Path(work_dir) / ".nonocode" / "permissions.yaml",
                local_rules_path=Path(work_dir) / ".nonocode" / "permissions.local.yaml",
            ),
            mode=PermissionMode(getattr(self._config, "permission_mode", "default")),
        )

        # 加载自定义指令和记忆
        instructions = load_instructions(work_dir)
        self.memory_manager = MemoryManager(work_dir)
        self.session_manager = SessionManager(work_dir)
        self.session = self.session_manager.create()
        self.session_id = self.session.session_id

        # 创建 LLM 客户端
        client = create_client(provider)

        # 工具注册表
        self.registry = create_default_registry(web_config=getattr(self._config, "web", {}))
        self.registry.register(ToolSearchTool(self.registry, protocol=provider.protocol))

        # Skill 加载
        self.skill_loader = SkillLoader(work_dir)
        self.skill_loader.load_all()
        load_skill_tool = LoadSkill()
        self.registry.register(load_skill_tool)

        # 创建 Agent
        self.agent = Agent(
            client=client,
            registry=self.registry,
            protocol=provider.protocol,
            work_dir=work_dir,
            permission_checker=checker,
            context_window=provider.get_context_window(),
            instructions_content=instructions,
            memory_manager=self.memory_manager,
            hook_engine=self.hook_engine,
            run_limits=getattr(self._config, "run_limits", None),
        )
        self.agent.session_id = self.session_id
        reset_agent_session(self.agent, self.session_id)

        # 团队工具在 remote 模式下同样可用，Lead 能在浏览器会话里组建团队把活派出去
        from nonocode.agents.loader import AgentLoader
        from nonocode.agents.task_manager import TaskManager
        from nonocode.agents.trace import TraceManager
        from nonocode.config import WorktreeConfig
        from nonocode.teams.manager import TeamManager
        from nonocode.tools.agent_tool import AgentTool
        from nonocode.tools.synthetic_output import SyntheticOutputTool
        from nonocode.tools.task_stop import TaskStopTool
        from nonocode.tools.team_create import TeamCreateTool
        from nonocode.tools.team_delete import TeamDeleteTool
        from nonocode.worktree import WorktreeManager

        cfg = self._config
        enable_fork = getattr(cfg, "enable_fork", False)
        enable_verification = getattr(cfg, "enable_verification_agent", False)
        enable_coordinator = getattr(cfg, "enable_coordinator_mode", False)
        wt_cfg = getattr(cfg, "worktree", None) or WorktreeConfig()

        wt_manager = WorktreeManager(
            repo_root=work_dir,
            symlink_directories=wt_cfg.symlink_directories,
        )
        trace_manager = TraceManager()
        self.task_manager = TaskManager()
        agent_loader = AgentLoader(work_dir, enable_verification=enable_verification)
        agent_loader.load_all()
        self.team_manager = TeamManager(
            worktree_manager=wt_manager, trace_manager=trace_manager
        )

        self.registry.register(AgentTool(
            agent_loader=agent_loader,
            task_manager=self.task_manager,
            trace_manager=trace_manager,
            parent_agent=self.agent,
            enable_fork=enable_fork,
            provider_config=provider,
            worktree_manager=wt_manager,
            team_manager=self.team_manager,
        ))
        self.registry.register(TeamCreateTool(
            team_manager=self.team_manager,
            parent_agent=self.agent,
            teammate_mode="in-process",
            is_interactive=False,
            enable_coordinator_mode=enable_coordinator,
        ))
        self.registry.register(TeamDeleteTool(
            team_manager=self.team_manager, parent_agent=self.agent
        ))
        self.registry.register(TaskStopTool(team_manager=self.team_manager))
        self.registry.register(SyntheticOutputTool())

        # 队员干完活的回传落在 lead 信箱里，每轮排空成 system-reminder 交给 Lead
        self.agent.notification_fn = self.team_manager.drain_lead_mailbox

        # 连接 Skill 到 Agent
        load_skill_tool.set_loader(self.skill_loader)
        load_skill_tool.set_agent(self.agent)
        from nonocode.skills.executor import SkillExecutor
        from nonocode.commands.handlers.skill_register import register_skill_commands
        self.skill_executor = SkillExecutor(self.agent, client, provider.protocol)
        register_skill_commands(self.command_registry, self.skill_loader, self.skill_executor)

        catalog = self.skill_loader.get_catalog()
        if catalog:
            lines = ["You can use the following Skills:", ""]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append("If the user's request matches a Skill, call LoadSkill to activate it.")
            self.agent.set_skill_catalog("\n".join(lines))

        # 初始化对话管理器
        self.conversation = ConversationManager()

        log.info("Agent initialized: session=%s, model=%s", self.session_id, provider.model)

    # ------------------------------------------------------------------
    # MCP 初始化
    # ------------------------------------------------------------------

    async def _init_mcp(self) -> None:
        """连接所有配置的 MCP 服务器，注册工具。"""
        if not self._mcp_server_configs or self.registry is None:
            return

        manager = MCPManager()
        manager.load_configs(self._mcp_server_configs)
        connect_result = await manager.register_all_tools(self.registry)
        self.mcp_manager = manager

        for err in connect_result.errors:
            log.warning("MCP error: %s", err)

        # 构建 MCP 指令（首次发送消息时注入 conversation）
        if connect_result.servers:
            self._mcp_server_info = (
                f"Connected to {len(connect_result.servers)} MCP server(s), "
                f"{len(connect_result.tools)} tools registered"
            )
            parts = []
            for srv_info in connect_result.servers:
                section = f"## {srv_info.name}\n"
                if srv_info.instructions:
                    section += srv_info.instructions
                else:
                    tool_names = [
                        t.name for t in self.registry.list_tools()
                        if getattr(t, "server_name", None) == srv_info.name
                    ]
                    if tool_names:
                        section += "Available tools: " + ", ".join(tool_names)
                parts.append(section)
            self._mcp_instructions = (
                "# MCP Server Instructions\n\n"
                "The following MCP servers have provided instructions "
                "for how to use their tools and resources:\n\n"
                + "\n\n".join(parts)
            )

    # ------------------------------------------------------------------
    # 用户消息处理
    # ------------------------------------------------------------------

    async def _handle_user_message(self, content: str) -> None:
        try:
            await self._handle_user_message_impl(content)
        except (sqlite3.Error,OSError) as exc:
            log.exception('Local persistence failed')
            self._streaming = False
            self._current_run_id = None
            self._agent_task = None
            self._cancel_event = None
            for future in self._pending_perms.values():
                if not future.done():
                    future.cancel()
            self._pending_perms.clear()
            self._pending_perm_details.clear()
            await self._broadcast({'type':'error','data':{'message':f'本地记录保存失败: {exc}'}})

    async def _handle_user_message_impl(self, content: str) -> None:
        """处理来自 Web UI 的用户消息或斜杠命令。"""
        if self._lab_task and not self._lab_task.done():
            await self._broadcast({'type':'system','data':{'message':'能力验证正在进行；请在进化实验室查看进度或取消后继续对话。'}})
            return
        if self._streaming and not content.startswith('/'):
            return

        # 斜杠命令
        if content.startswith("/"):
            await self._handle_slash_command(content)
            return

        # 普通消息 → 发给 Agent
        self._streaming = True
        self._agent_task = asyncio.current_task()
        assert self.conversation is not None
        assert self.agent is not None

        self._begin_replay()
        self._remember_event({"type":"replay_user", "data":{"content":content}})
        self.conversation.add_user_message(content)
        if self.session:
            self.session.append(Message(role="user", content=content))
        history_cursor = self.conversation.history[-1] if self.conversation.history else None

        # 首次注入 MCP 指令
        if self._mcp_instructions:
            self.conversation.add_system_reminder(self._mcp_instructions)
            self._mcp_instructions = ""

        # 创建取消事件
        self._cancel_event = asyncio.Event()
        start_time = time.monotonic()
        stream_buf = ""

        run = TrackedRun(self.session_manager.database, self.session_id, content, 'remote',
                         input_offset=self.agent.total_input_tokens, output_offset=self.agent.total_output_tokens)
        self._current_run_id = run.id
        tracked_events = run.events(self.agent.run(self.conversation))
        terminal_message = None
        try:
            async for event in tracked_events:
                # 检查取消信号
                if self._cancel_event.is_set():
                    break

                if isinstance(event, StreamText):
                    stream_buf += event.text
                    await self._broadcast({
                        "type": "stream_text",
                        "data": {"text": event.text},
                    })

                elif isinstance(event, ThinkingText):
                    await self._broadcast({
                        "type": "thinking_text",
                        "data": {"text": event.text},
                    })

                elif isinstance(event, ToolUseEvent):
                    await self._broadcast({
                        "type": "tool_use",
                        "data": {
                            "toolId": event.tool_id,
                            "toolName": event.tool_name,
                            "args": event.arguments,
                        },
                    })

                elif isinstance(event, ToolResultEvent):
                    # 如果之前有累积的流式文本，先结束它
                    if stream_buf:
                        await self._broadcast({
                            "type": "stream_end",
                            "data": {"text": stream_buf},
                        })
                        stream_buf = ""
                    await self._broadcast({
                        "type": "tool_result",
                        "data": {
                            "toolId": event.tool_id,
                            "toolName": event.tool_name,
                            "output": event.output,
                            "isError": event.is_error,
                            "elapsed": event.elapsed,
                        },
                    })

                elif isinstance(event, PermissionRequest):
                    # 生成唯一 ID，等待 Web 端回复
                    perm_id = f"perm_{time.time_ns()}"
                    self._pending_perms[perm_id] = event.future
                    self._pending_perm_details[perm_id] = {"id":perm_id,"toolName":event.tool_name,"description":event.description}
                    await self._broadcast({
                        "type": "permission_request",
                        "data": {
                            "id": perm_id,
                            "toolName": event.tool_name,
                            "description": event.description,
                        },
                    })

                elif isinstance(event, TurnComplete):
                    history_cursor = self._persist_messages(history_cursor)
                    if stream_buf:
                        await self._broadcast({
                            "type": "stream_end",
                            "data": {"text": stream_buf},
                        })
                        stream_buf = ""
                    await self._broadcast({
                        "type": "turn_complete",
                        "data": {"turn": event.turn},
                    })

                elif isinstance(event, LoopComplete):
                    if terminal_message and terminal_message.get("type") == "error":
                        continue
                    history_cursor = self._persist_messages(history_cursor)
                    if stream_buf:
                        await self._broadcast({
                            "type": "stream_end",
                            "data": {"text": stream_buf},
                        })
                        stream_buf = ""
                    elapsed = time.monotonic() - start_time
                    terminal_message = {
                        "type": "loop_complete",
                        "data": {
                            "totalTurns": event.total_turns,
                            "elapsed": elapsed,
                        },
                    }

                elif isinstance(event, UsageEvent):
                    await self._broadcast({
                        "type": "usage",
                        "data": {
                            "inputTokens": event.input_tokens,
                            "outputTokens": event.output_tokens,
                        },
                    })

                elif isinstance(event, ErrorEvent):
                    terminal_message = {
                        "type": "error",
                        "data": {"message": event.message},
                    }

                elif isinstance(event, CompactNotification):
                    if self.session and event.boundary is not None:
                        from nonocode.memory.session import make_compact_boundary
                        self.session.append_record(make_compact_boundary(
                            event.boundary.summary, event.boundary.keep,
                        ))
                        history_cursor = self.conversation.history[-1] if self.conversation.history else None
                    await self._broadcast({
                        "type": "compact",
                        "data": {"message": event.message},
                    })

                elif isinstance(event, RetryEvent):
                    await self._broadcast({
                        "type": "retry",
                        "data": {
                            "reason": event.reason,
                            "waitMs": int(event.wait * 1000),
                        },
                    })

                elif isinstance(event, HookEvent):
                    status = "ok" if event.success else "error"
                    await self._broadcast({
                        "type": "system",
                        "data": {
                            "message": f"Hook [{event.hook_id}] {status}: {event.output}"
                        },
                    })

        except asyncio.CancelledError:
            run.finish('cancelled','用户取消了本次执行')
            terminal_message = {
                "type": "error",
                "data": {"message": "Operation cancelled"},
            }
        except Exception as exc:
            run.finish('failed',str(exc))
            log.exception("Agent run error")
            terminal_message = {
                "type": "error",
                "data": {"message": str(exc)},
            }
        finally:
            await tracked_events.aclose()
            self._persist_messages(history_cursor)
            if self.session:
                self.session.meta.total_tokens = self._session_token_offset + self.agent.total_input_tokens + self.agent.total_output_tokens
                self.session.meta.save(
                    self.session._sessions_dir / f"{self.session.session_id}.meta"
                )
            self._current_run_id = None
            self._streaming = False
            self._cancel_event = None
            self._agent_task = None
            for future in self._pending_perms.values():
                if not future.done():
                    future.cancel()
            self._pending_perms.clear()
            self._pending_perm_details.clear()
            await self._broadcast({"type":"run_state", "data":{"streaming":False,"runId":None}})
            if terminal_message is not None:
                await self._broadcast(terminal_message)

    def cancel_run(self, run_id: str) -> bool:
        if self._lab_task and not self._lab_task.done() and run_id == self._lab_job.get('run_id'):
            self._lab_task.cancel()
            return True
        if run_id != self._current_run_id or self._agent_task is None or self._agent_task.done():
            return False
        self._agent_task.cancel()
        return True

    def _persist_messages(self, cursor: Message | None) -> Message | None:
        if self.session is not None and self.conversation is not None:
            return self.session.append_new_messages(self.conversation.history, cursor)
        return cursor

    # ------------------------------------------------------------------
    # 斜杠命令处理
    # ------------------------------------------------------------------

    async def _handle_slash_command(self, input_text: str) -> None:
        """分发斜杠命令。"""
        name, args, is_command = parse_command(input_text)
        if not is_command or not name:
            return

        cmd = self.command_registry.find(name)
        if cmd is None:
            await self._broadcast({
                "type": "error",
                "data": {"message": f"Unknown command: /{name} — type /help to see available commands"},
            })
            await self._broadcast({"type": "command_done", "data": None})
            return

        from nonocode.commands.registry import requires_idle
        if self._streaming and requires_idle(cmd,args):
            await self._broadcast({'type':'system','data':{'message':'请等待或取消当前任务后再执行此操作'}})
            await self._broadcast({'type':'command_done','data':None})
            return

        # 需要参数但没给
        if not args and cmd.arg_prompt:
            await self._broadcast({
                "type": "system",
                "data": {"message": cmd.arg_prompt},
            })
            await self._broadcast({"type": "command_done", "data": None})
            return

        if cmd.type == CommandType.LOCAL:
            # 本地命令直接执行
            ctx = self._build_command_context(args)
            try:
                await cmd.handler(ctx)
            except Exception as exc:
                await self._broadcast({
                    "type": "error",
                    "data": {"message": f"Command error: {exc}"},
                })
            await self._broadcast({"type": "command_done", "data": None})

        elif cmd.type == CommandType.LOCAL_UI:
            # UI 命令需要特殊处理
            if cmd.name in {"clear", "plan"}:
                try:
                    await cmd.handler(self._build_command_context(args))
                except Exception as exc:
                    await self._broadcast({"type": "error", "data": {"message": f"Command error: {exc}"}})

            elif name == "compact":
                await self._handle_compact()
                return

            else:
                await self._broadcast({
                    "type": "system",
                    "data": {"message": f"/{name} is not fully supported in remote mode."},
                })

            await self._broadcast({"type": "command_done", "data": None})

        elif cmd.type == CommandType.PROMPT:
            # Prompt 类命令：handler 返回 prompt 文本，注入给 agent
            ctx = self._build_command_context(args)
            try:
                await cmd.handler(ctx)
            except Exception as exc:
                await self._broadcast({
                    "type": "error",
                    "data": {"message": f"Command error: {exc}"},
                })
                await self._broadcast({"type": "command_done", "data": None})

    def _build_command_context(self, args: str) -> CommandContext:
        """构建命令上下文。"""
        return CommandContext(
            args=args,
            agent=self.agent,
            conversation=self.conversation,
            session=self.session,
            session_manager=self.session_manager,
            memory_manager=self.memory_manager,
            ui=self,  # type: ignore[arg-type]
            config={
                "registry": self.command_registry,
                "set_session": self._set_session,
                "set_conversation": self._set_conversation,
                "clear_chat": self._clear_chat,
                "render_restored": self._render_restored_messages,
                "skill_loader": self.skill_loader,
                "skill_executor": getattr(self, "skill_executor", None),
            },
        )

    def _set_session(self, session: Session) -> None:
        self._replay_base = None
        self._event_log.clear()
        self._event_log_bytes = 0
        self._replay_truncated = False
        self._session_token_offset = session.meta.total_tokens
        self.session = session
        self.session_id = session.session_id
        if self.agent:
            self.agent.session_id = self.session_id

    def _set_conversation(self, conversation: ConversationManager) -> None:
        self.conversation = conversation

    def _clear_chat(self) -> None:
        asyncio.create_task(self._broadcast({"type": "clear", "data": None}))

    async def _render_restored_messages(self, messages: list[Message]) -> None:
        await self._broadcast({"type": "clear", "data": None})
        for message in messages:
            if message.role == "user" and message.content and not message.content.startswith("<system-reminder>"):
                await self._broadcast({"type": "replay_user", "data": {"content": message.content}})
            elif message.role == "assistant" and message.content:
                await self._broadcast({"type": "replay_assistant", "data": {"content": message.content}})

    async def _handle_compact(self) -> None:
        """处理 /compact 命令。"""
        if self.agent is None or self.conversation is None:
            await self._broadcast({
                "type": "error",
                "data": {"message": "Compact requires an active agent."},
            })
            await self._broadcast({"type": "command_done", "data": None})
            return

        await self._broadcast({
            "type": "system",
            "data": {"message": "Compacting conversation..."},
        })

        result = await self.agent.manual_compact(self.conversation)
        if isinstance(result, CompactNotification):
            await self._broadcast({
                "type": "system",
                "data": {"message": result.message},
            })
        elif isinstance(result, ErrorEvent):
            await self._broadcast({
                "type": "error",
                "data": {"message": result.message},
            })

        await self._broadcast({"type": "command_done", "data": None})

    # ------------------------------------------------------------------
    # UIController 协议实现（供命令系统回调）
    # ------------------------------------------------------------------

    def add_system_message(self, text: str) -> None:
        """同步接口 — 在事件循环中调度广播。"""
        asyncio.ensure_future(self._broadcast({
            "type": "system",
            "data": {"message": text},
        }))

    def send_user_message(self, text: str) -> None:
        """同步接口 — 注入用户消息并触发 agent。"""
        asyncio.create_task(self._handle_user_message(text))

    def set_plan_mode(self, enabled: bool) -> None:
        if self.agent is None:
            return
        if enabled:
            self.agent.set_permission_mode(PermissionMode.PLAN)
        else:
            self.agent.set_permission_mode(PermissionMode.DEFAULT)

    def get_token_count(self) -> tuple[int, int]:
        if self.agent:
            return self.agent.total_input_tokens, self.agent.total_output_tokens
        return 0, 0

    def refresh_status(self) -> None:
        pass  # Remote 模式不需要刷新 TUI 状态栏

    # ------------------------------------------------------------------
    # 权限响应处理
    # ------------------------------------------------------------------

    def _handle_permission_response(self, data: dict[str, Any]) -> None:
        """处理来自 Web UI 的权限回复。"""
        perm_id = data.get("id", "")
        response_str = data.get("response", "deny")

        future = self._pending_perms.pop(perm_id, None)
        self._pending_perm_details.pop(perm_id, None)
        if future is None or future.done():
            return

        # 映射字符串到枚举
        mapping = {
            "allow": PermissionResponse.ALLOW,
            "deny": PermissionResponse.DENY,
            "allowAlways": PermissionResponse.ALLOW_ALWAYS,
        }
        response = mapping.get(response_str, PermissionResponse.DENY)
        future.set_result(response)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _build_command_list(self) -> list[dict[str, str]]:
        """构建命令列表，推送给前端用于斜杠命令菜单。"""
        result = []
        for cmd in self.command_registry.list_commands():
            result.append({
                "name": cmd.name,
                "description": cmd.description,
            })
        return result

    def _chat_snapshot(self):
        messages = []
        for message in (self.conversation.history if self.conversation else []):
            if message.role in {'user','assistant'} and message.content and not message.content.startswith(('<system-reminder>', '<nonocode-')):
                messages.append({"type":"replay_user" if message.role == 'user' else "replay_assistant",
                                 "data":{"content":message.content[:20000]}})
        return messages[-50:]

    def _begin_replay(self):
        self._replay_base = self._chat_snapshot()
        self._event_log.clear()
        self._event_log_bytes = 0
        self._replay_truncated = False

    def _remember_event(self, msg):
        if msg['type'] == 'clear':
            self._replay_base = []
            self._event_log.clear()
            self._event_log_bytes = 0
            self._replay_truncated = False
            return
        if msg['type'] not in {'replay_user','replay_assistant','stream_text','stream_end','thinking_text',
            'tool_use','tool_result','loop_complete','turn_complete','usage','error','retry','compact','run_state'}:
            return
        size = len(json.dumps(msg, ensure_ascii=False).encode('utf-8'))
        self._event_log.append((msg,size))
        self._event_log_bytes += size
        while self._event_log and (self._event_log_bytes > 1_000_000 or len(self._event_log) > 3000):
            _, old_size = self._event_log.popleft()
            self._event_log_bytes -= old_size
            self._replay_truncated = True

    def _state_snapshot(self):
        pending = [self._pending_perm_details[key] for key, future in self._pending_perms.items()
                   if not future.done() and key in self._pending_perm_details]
        return {"session":self.session_id,"runId":self._current_run_id,"streaming":self._streaming,
                "history":self._replay_base if self._replay_base is not None else self._chat_snapshot(),
                "events":[msg for msg,_ in self._event_log], "permissions":pending,
                "truncated":self._replay_truncated}

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        async with self._broadcast_lock:
            self._remember_event(msg)
            if not self._connections:
                return
            data = json.dumps(msg, ensure_ascii=False)
            async def send(ws):
                try:
                    await asyncio.wait_for(ws.send(data), timeout=5)
                except (Exception, asyncio.CancelledError):
                    self._connections.discard(ws)
                    if asyncio.current_task().cancelling():
                        raise
            await asyncio.gather(*(send(ws) for ws in list(self._connections)))
