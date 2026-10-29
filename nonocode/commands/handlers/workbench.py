from __future__ import annotations
import asyncio
import json
import shlex
from pathlib import Path
from nonocode.commands.registry import Command,CommandType


async def artifacts(ctx):
    from nonocode.storage.products import ProductStore
    rows=await asyncio.to_thread(ProductStore(ctx.session_manager.database).artifacts,ctx.args)
    ctx.ui.add_system_message('\n'.join(f"{row['id']} v{row['version']} {row['relative_path']} ({row['size']} bytes)" for row in rows) or '暂无产物')


async def sources(ctx):
    from nonocode.storage.products import ProductStore
    rows=await asyncio.to_thread(ProductStore(ctx.session_manager.database).sources,ctx.args)
    ctx.ui.add_system_message('\n'.join(f"{row['title']}\n{row['url']} · {row['retrieved_at']}" for row in rows) or '暂无来源')


async def budget(ctx):
    from nonocode.execution import usage_summary
    result=await asyncio.to_thread(usage_summary,ctx.session_manager.database,ctx.args.strip())
    ctx.ui.add_system_message(json.dumps(result,ensure_ascii=False,indent=2))


async def settings(ctx):
    from nonocode.settings import SettingsService
    service=SettingsService(ctx.agent.work_dir,ctx.session_manager.database)
    args=shlex.split(ctx.args)
    try:
        if args and args[0]=='save':
            if len(args)!=2:raise ValueError('用法：/settings save <配置 JSON 文件>')
            document=json.loads(Path(args[1]).read_text())
            result=await asyncio.to_thread(service.save,document['values'],document['revision'])
            result['message']='配置已保存，终端请重新启动以应用模型变更'
        elif args and args[0]=='test':
            result=await service.test(service.read()['values'],session_id=ctx.agent.session_id)
        else:result=await asyncio.to_thread(service.read)
        ctx.ui.add_system_message(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError) as exc:ctx.ui.add_system_message(str(exc))


WORKBENCH_COMMANDS=[Command(name=name,description=description,usage=usage,type=CommandType.LOCAL,handler=handler) for name,description,usage,handler in [
    ('artifacts','查看任务产物','/artifacts [关键词]',artifacts),('sources','查看联网来源','/sources [关键词]',sources),
    ('budget','查看全链路用量','/budget [根任务ID]',budget),('settings','配置及模型连接测试','/settings [save <JSON文件> | test]',settings)]]
