"""Controlled browser fixture: real server/tools/storage, scripted model responses."""
import asyncio
import json
import os
from pathlib import Path
import sys

import httpx
import websockets

from nonocode.client import LLMClient
from nonocode.config import AppConfig,ProviderConfig
from nonocode.evolution.demo import DemoClient
from nonocode.remote import RemoteServer
from nonocode.tools.base import StreamEnd,TextDelta,ToolCallComplete
from nonocode.tools.web import PublicHTTP,WebFetch


class BrowserClient(LLMClient):
    def __init__(self,provider):self._usage_config=provider;self.model=provider.model
    async def stream(self,conversation,system='',tools=None):
        if any('\n\nInput files: ' in message.content for message in conversation.history if message.role=='user'):
            async for event in DemoClient().stream(conversation,system,tools):yield event
            return
        prompt=next((message.content for message in reversed(conversation.history) if message.role=='user' and message.content and not message.content.startswith('<')),'')
        if prompt=='Reply with exactly OK.':
            yield TextDelta('OK');yield StreamEnd('end_turn',3,1);return
        if prompt=='Wait for cancellation':
            yield TextDelta('Controlled waiting response')
            await asyncio.sleep(30)
            yield StreamEnd('end_turn',2,1);return
        user_index=max((index for index,message in enumerate(conversation.history) if message.role=='user' and message.content==prompt),default=0)
        called=any(message.tool_uses for message in conversation.history[user_index+1:])
        if not called:
            yield ToolCallComplete('browser-file','WriteFile',{'file_path':str(Path.cwd()/'browser-report.md'),'content':'# Controlled browser report\nTest fixture evidence.\n'})
            yield ToolCallComplete('browser-source','WebFetch',{'url':'https://example.com/controlled-evidence'})
            yield StreamEnd('tool_use',10,3)
        else:
            yield TextDelta('Report created with a source snapshot.');yield StreamEnd('end_turn',10,4)


async def main():
    import nonocode.remote
    import nonocode.client
    nonocode.remote.create_client=lambda provider:BrowserClient(provider)
    nonocode.client.create_client=lambda provider:BrowserClient(provider)
    path=Path('.nonocode');path.mkdir(exist_ok=True)
    config=path/'config.yaml'
    config.write_text('providers:\n- name: browser-fixture\n  protocol: anthropic\n  base_url: https://example.com\n  model: controlled\n  api_key: fixture-only-key\npermission_mode: acceptEdits\n')
    provider=ProviderConfig('browser-fixture','anthropic','https://example.com','controlled',api_key='fixture-only-key')
    server=RemoteServer([provider],config=AppConfig([provider],permission_mode='acceptEdits'));server._init_agent()
    server.agent._consolidator=None
    async def no_memory(*args,**kwargs):pass
    server.memory_manager.extract=no_memory
    async def resolver(host,port):return ['93.184.216.34']
    def response(request):return httpx.Response(200,headers={'content-type':'text/html'},text='<title>Controlled evidence</title><p>Fixture fact from a controlled HTTP transport.</p>')
    original_apply=server._apply_workbench_config
    def apply(config):
        original_apply(config)
        server.registry.register(WebFetch(PublicHTTP(transport=httpx.MockTransport(response),resolver=resolver)))
    server._apply_workbench_config=apply
    server.registry.register(WebFetch(PublicHTTP(transport=httpx.MockTransport(response),resolver=resolver)))
    async with websockets.serve(server._ws_handler,'127.0.0.1',0,process_request=server._process_http_request,max_size=20_000_000) as listener:
        port=listener.sockets[0].getsockname()[1]
        Path(sys.argv[1]).write_text(str(port))
        try:await asyncio.Future()
        finally:
            for task in (server._agent_task,server._lab_task):
                if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
            server.session.close()


asyncio.run(main())
