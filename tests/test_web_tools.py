import asyncio
import json
from pathlib import Path

import httpx
import pytest

from nonocode.config import AppConfig, ProviderConfig, _merge_config, load_config
from nonocode.limits import RunLimits
from nonocode.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from nonocode.tools import create_default_registry
from nonocode.tools.web import (FetchParams, SearchParams, PublicHTTP, WebFetch, WebSearch,
                                WebError, PageText, public_url, public_addresses, MAX_BYTES)
from nonocode.validator import ConfigError, validate_run_limits, validate_web


async def resolver(host,port):
    return ['93.184.216.34']


def client(handler):
    return PublicHTTP(transport=httpx.MockTransport(handler),resolver=resolver)


@pytest.mark.parametrize('value', [
    'file:///etc/passwd','ftp://example.com/a','http://localhost/','http://127.0.0.1/',
    'http://169.254.169.254/latest/meta-data/','http://10.2.3.4/','http://[::1]/',
    'https://name:secret@example.com/','http://example.com:8888/','https://test.local/',
    'https://x.internal/','http://[::ffff:127.0.0.1]/','http://[fe80::1%25eth0]/',
])
def test_block_nonpublic_urls(value):
    with pytest.raises(WebError):
        public_url(value)


@pytest.mark.asyncio
async def test_pinned_dns_preserves_host_sni_and_html_evidence():
    def handler(request):
        assert request.url.host=='93.184.216.34'
        assert request.headers['host']=='example.com'
        assert request.extensions['sni_hostname']=='example.com'
        return httpx.Response(200,headers={'content-type':'text/html; charset=utf-8'},
            text='<title>Example &amp; test</title><nav>Menu</nav><p>Evidence 中文</p>'
                 '<script>steal()</script><div hidden>Hidden</div><p>End</p>')
    result=await WebFetch(client(handler)).execute(FetchParams(url='https://example.com/test?q=1#fragment'))
    assert not result.is_error
    data=json.loads(result.output)
    assert data['url']=='https://example.com/test?q=1'
    assert data['title']=='Example & test'
    assert data['text']=='Evidence 中文\nEnd'
    assert data['retrieved_at'] and data['untrusted']


@pytest.mark.asyncio
async def test_rebinding_and_redirect_into_private_network_are_blocked():
    visited=[]
    def handler(request):
        visited.append(str(request.url))
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    result=await WebFetch(client(handler)).execute(FetchParams(url='https://example.com/'))
    assert result.is_error and len(visited)==1
    async def bad_dns(host,port):
        return ['93.184.216.34','10.0.0.1']
    bad=PublicHTTP(transport=httpx.MockTransport(handler),resolver=bad_dns)
    result=await WebFetch(bad).execute(FetchParams(url='https://example.com/'))
    assert result.is_error and len(visited)==1


@pytest.mark.asyncio
async def test_actual_dns_validator_rejects_private_answers(monkeypatch):
    loop=asyncio.get_running_loop()
    async def getaddrinfo(*args,**kwargs):
        return [(2,1,6,'',('127.0.0.1',443))]
    monkeypatch.setattr(loop,'getaddrinfo',getaddrinfo)
    with pytest.raises(WebError):
        await public_addresses('public-looking.test',443)


@pytest.mark.asyncio
async def test_redirect_count_is_bounded():
    visits=[]
    def handler(request):
        visits.append(request)
        return httpx.Response(302,headers={'location':'/loop'})
    result=await WebFetch(client(handler)).execute(FetchParams(url='https://example.com/'))
    assert result.is_error and len(visits)==6


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['status','binary','length','stream_length','timeout','empty'])
async def test_fetch_failure_paths(kind):
    async def handler(request):
        if kind=='status':return httpx.Response(403,text='login')
        if kind=='binary':return httpx.Response(200,headers={'content-type':'application/pdf'},content=b'%PDF')
        if kind=='length':return httpx.Response(200,headers={'content-length':str(MAX_BYTES+1)},text='x')
        if kind=='stream_length':return httpx.Response(200,content=b'x'*(MAX_BYTES+1))
        if kind=='timeout':raise httpx.ReadTimeout('secret query string must not leak')
        return httpx.Response(200,headers={'content-type':'text/html'},text='<script>only code</script>')
    result=await WebFetch(client(handler)).execute(FetchParams(url='https://example.com/'))
    assert result.is_error and 'secret' not in result.output


@pytest.mark.asyncio
async def test_fetch_plain_json_and_truncation():
    http=client(lambda request:httpx.Response(200,headers={'content-type':'application/json'},text='x'*600))
    data=json.loads((await WebFetch(http).execute(FetchParams(url='https://example.com/',max_chars=500))).output)
    assert data['truncated'] and len(data['text'])==500 and data['title']==''


@pytest.mark.asyncio
async def test_bing_search_parses_sources_and_filters_private_duplicates():
    rss='''<rss><channel><item><title>Public</title><link>https://example.com/</link><description>&lt;b&gt;Evidence&lt;/b&gt;</description></item>
    <item><title>Private</title><link>http://127.0.0.1/secret</link></item>
    <item><title>Duplicate</title><link>https://example.com/</link></item></channel></rss>'''
    def handler(request):
        assert request.headers['host']=='www.bing.com'
        assert request.url.params['q']=='中文 Python'
        assert request.url.params['format']=='rss'
        return httpx.Response(200,headers={'content-type':'application/rss+xml'},text=rss)
    result=await WebSearch(http=client(handler)).execute(SearchParams(query='中文 Python'))
    data=json.loads(result.output)
    assert data['provider']=='bing' and data['untrusted']
    assert data['results']==[{'title':'Public','url':'https://example.com/','snippet':'Evidence'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('response', ['<html>captcha</html>','<rss><bad>','<!DOCTYPE rss><rss/>'])
async def test_search_captcha_is_a_failure(response):
    http=client(lambda request:httpx.Response(200,headers={'content-type':'text/html'},text=response))
    result=await WebSearch(http=http).execute(SearchParams(query='query'))
    assert result.is_error and 'failed' in result.output


@pytest.mark.asyncio
async def test_brave_uses_env_credentials_and_never_forwards_token(monkeypatch):
    monkeypatch.setenv('NONOCODE_TEST_SEARCH_KEY','test-secret')
    seen=[]
    def handler(request):
        seen.append(request)
        if len(seen)==1:
            assert request.headers['x-subscription-token']=='test-secret'
            return httpx.Response(302,headers={'location':'https://other.example/results'})
        assert 'x-subscription-token' not in request.headers
        return httpx.Response(200,json={'web':{'results':[{'title':'Source','url':'https://example.com','description':'Text'}]}})
    tool=WebSearch({'search_provider':'brave','search_api_key_env':'NONOCODE_TEST_SEARCH_KEY'},client(handler))
    result=await tool.execute(SearchParams(query='query'))
    assert not result.is_error and 'test-secret' not in result.output
    assert json.loads(result.output)['results'][0]['title']=='Source'


@pytest.mark.asyncio
async def test_missing_search_key_is_actionable(monkeypatch):
    monkeypatch.delenv('BRAVE_SEARCH_API_KEY',raising=False)
    result=await WebSearch({'search_provider':'brave'}).execute(SearchParams(query='query'))
    assert result.is_error and 'BRAVE_SEARCH_API_KEY' in result.output


def test_registry_and_permission_rules_cover_network_tools(tmp_path):
    registry=create_default_registry()
    assert {'WebSearch','WebFetch'} <= {tool.name for tool in registry.list_tools()}
    assert create_default_registry(web_config={'enabled':False}).get('WebFetch') is None
    rules=tmp_path/'rules.yaml'
    rules.write_text('- rule: "WebFetch(https://blocked.example/*)"\n  effect: deny\n')
    checker=PermissionChecker(DangerousCommandDetector(),PathSandbox(str(tmp_path)),
                              RuleEngine(project_rules_path=rules),PermissionMode.PLAN)
    assert checker.check(registry.get('WebFetch'),{'url':'https://example.com/'}).effect=='allow'
    assert checker.check(registry.get('WebFetch'),{'url':'https://blocked.example/'}).effect=='deny'


@pytest.mark.parametrize('value',[{'max_seconds':-1},{'max_tokens':True},{'typo':1},'bad'])
def test_invalid_limit_config_is_rejected(value):
    with pytest.raises(ConfigError):validate_run_limits(value)


def test_explicit_zero_overrides_limit_in_layered_config(tmp_path):
    base=AppConfig([],run_limits=RunLimits(max_seconds=30),web={'search_provider':'brave'})
    override=AppConfig([],run_limits=RunLimits(max_seconds=0),_run_limit_fields={'max_seconds'},web={'enabled':False})
    result=_merge_config(base,override)
    assert result.run_limits.max_seconds==0 and result.web=={'search_provider':'brave','enabled':False}
    f=tmp_path/'config.yaml'
    f.write_text('providers:\n- name: test\n  protocol: anthropic\n  base_url: https://example.com\n  model: test\nrun_limits:\n  max_tool_calls: 12\nweb:\n  enabled: false\n')
    result=load_config(f)
    assert result.run_limits.max_tool_calls==12 and result.web['enabled'] is False


@pytest.mark.asyncio
async def test_ipv6_failure_tries_next_checked_address_and_decodes_html_charset():
    async def addresses(host,port):return ['2606:4700:4700::1111','93.184.216.34']
    visited=[]
    def handler(request):
        visited.append(request.url.host)
        if len(visited)==1:raise httpx.ConnectError('no IPv6 route')
        return httpx.Response(200,headers={'content-type':'text/html'},content='<meta charset="gb18030"><p>中文内容</p>'.encode('gb18030'))
    http=PublicHTTP(transport=httpx.MockTransport(handler),resolver=addresses)
    result=await WebFetch(http).execute(FetchParams(url='https://example.com/'))
    assert not result.is_error and json.loads(result.output)['text']=='中文内容'
    assert len(visited)==2


def test_limits_only_local_config_merges_without_redeclaring_provider(tmp_path,monkeypatch):
    project=tmp_path/'project';home=tmp_path/'home'
    (project/'.nonocode').mkdir(parents=True);home.mkdir()
    (project/'.nonocode/config.yaml').write_text('providers:\n- name: test\n  protocol: anthropic\n  base_url: https://example.com\n  model: test\nenable_fork: false\nrun_limits:\n  max_tokens: 1234\n')
    (project/'.nonocode/config.local.yaml').write_text('run_limits:\n  max_seconds: 0\nweb:\n  enabled: false\n')
    monkeypatch.chdir(project);monkeypatch.setattr(Path,'home',lambda:home)
    result=load_config()
    assert result.providers[0].model=='test' and result.enable_fork is False
    assert result.run_limits.max_seconds==0 and result.run_limits.max_tokens==1234
    assert result.web['enabled'] is False
