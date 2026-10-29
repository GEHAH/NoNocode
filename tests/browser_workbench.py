"""Standalone Chromium acceptance: configure, execute, export, reconnect, verify."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright,expect


def main():
    repository=Path(__file__).resolve().parents[1]
    output=Path(os.environ.get('NONOCODE_BROWSER_OUTPUT','/tmp/nonocode-browser-results'));output.mkdir(parents=True,exist_ok=True)
    workspace=output/'workspace';workspace.mkdir(exist_ok=True)
    (workspace/'ledger.csv').write_text('amount\n100\n-10\n')
    (workspace/'holdout.csv').write_text('amount\n7\n-2\n')
    portfile=output/'port';portfile.unlink(missing_ok=True)
    env=dict(os.environ,HOME=str(output/'home'),NONOCODE_DATA_DIR=str(output/'database'),PYTHONPATH=str(repository)+os.pathsep+os.environ.get('PYTHONPATH',''),PYTHONDONTWRITEBYTECODE='1')
    log=(output/'server.log').open('w')
    server=subprocess.Popen([sys.executable,str(repository/'tests/fixtures/workbench_server.py'),str(portfile)],cwd=workspace,env=env,stdout=log,stderr=log)
    try:
        deadline=time.monotonic()+15
        while not portfile.exists():
            if server.poll() is not None:raise AssertionError((output/'server.log').read_text())
            if time.monotonic()>deadline:raise TimeoutError('fixture server not ready')
            time.sleep(.05)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1280,'height':900},accept_downloads=True)
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            # The application remains usable without its optional external Markdown CDN.
            page.route('**/cdn.jsdelivr.net/**',lambda route:route.abort())
            page.goto('http://127.0.0.1:'+portfile.read_text(),wait_until='domcontentloaded')
            expect(page.locator('#conn-status')).to_contain_text('Connected')
            page.get_by_role('button',name='配置',exact=True).click()
            expect(page.locator('#settings-model')).to_have_value('controlled')
            assert 'fixture-only-key' not in page.locator('body').inner_text()
            page.locator('#settings-model').fill('controlled-next')
            page.locator('#limit-max_tool_calls').fill('120')
            page.locator('#settings-save').click()
            expect(page.locator('#product-notice')).to_contain_text('已保存并应用')
            expect(page.locator('#settings-model')).to_have_value('controlled-next')
            page.get_by_role('button',name='测试当前模型连接',exact=True).click()
            expect(page.locator('#product-notice')).to_contain_text('完整模型响应')
            page.get_by_role('button',name='记忆',exact=True).click();page.locator('#new-memory').click()
            page.locator('#memory-name').fill('language');page.locator('#memory-body').fill('Use concise Chinese')
            page.locator('#memory-evidence').fill('Browser fixture preference');page.locator('#memory-topic').fill('language')
            page.get_by_role('button',name='保存记忆',exact=True).click()
            expect(page.locator('#panel-items')).to_contain_text('Browser fixture preference')
            page.get_by_role('button',name='对话',exact=True).click()
            page.locator('#input').fill('Create a report with sources');page.locator('#input').press('Enter')
            expect(page.locator('#messages')).to_contain_text('Report created with a source snapshot.',timeout=15000)
            expect(page.locator('#input')).to_be_enabled()
            page.get_by_role('button',name='产物',exact=True).click()
            expect(page.locator('#panel-items')).to_contain_text('browser-report.md')
            card=page.locator('.product-card').filter(has=page.locator('h3',has_text='browser-report.md')).first
            card.get_by_role('button',name='预览',exact=True).click()
            expect(page.locator('#workbench-fields')).to_contain_text('Controlled browser report');page.locator('#workbench-close').click()
            with page.expect_download() as download:card.get_by_role('button',name='连同来源导出',exact=True).click()
            download.value.save_as(output/'deliverable.zip')
            page.get_by_role('button',name='来源',exact=True).click()
            expect(page.locator('#panel-items')).to_contain_text('Controlled evidence')
            page.get_by_role('button',name='查看当时快照',exact=True).click()
            expect(page.locator('#workbench-fields')).to_contain_text('Fixture fact');page.locator('#workbench-close').click()
            page.get_by_role('button',name='用量与执行树',exact=True).click()
            expect(page.locator('#panel-items')).to_contain_text('controlled-next')
            page.get_by_role('button',name='对话',exact=True).click()
            page.locator('#input').fill('Wait for cancellation');page.locator('#input').press('Enter')
            expect(page.locator('#messages')).to_contain_text('Controlled waiting response')
            page.reload(wait_until='domcontentloaded');expect(page.locator('#messages')).to_contain_text('Controlled waiting response')
            page.get_by_role('button',name='任务中心',exact=True).click()
            page.locator('.product-card').filter(has=page.locator('h3',has_text='Wait for cancellation')).get_by_role('button',name='取消',exact=True).click()
            page.locator('#panel-search button').first.click()
            expect(page.locator('#panel-items')).to_contain_text('cancelled',timeout=10000)
            page.get_by_role('button',name='进化实验室',exact=True).click()
            page.get_by_role('button',name='新增候选',exact=True).click()
            page.locator('#candidate-rule').fill('When summing a signed ledger, include negative amounts as well as positive amounts.')
            page.locator('#candidate-cue').fill('Signed ledger tasks');page.locator('#workbench-submit').click()
            expect(page.locator('#panel-items')).to_contain_text('When summing a signed ledger')
            for identifier,filename,total,split in [('development','ledger.csv','90','development'),('holdout','holdout.csv','5','holdout')]:
                page.get_by_role('button',name='登记测试任务',exact=True).click()
                spec={'id':identifier,'prompt':'Sum signed amounts and write result.json with a string total.','inputs':[filename],
                    'checks':[{'type':'json_equals','path':'result.json','pointer':'/total','value':total}], 'split':split,'repetitions':2,'timeout_seconds':10,'max_iterations':8}
                page.locator('#task-spec').fill(json.dumps(spec));page.locator('#workbench-submit').click()
                expect(page.locator('#workbench-editor')).not_to_be_visible()
            page.get_by_role('button',name='运行对照验证',exact=True).click()
            page.locator('#task-choice-development').check();page.locator('#task-choice-holdout').check();page.locator('#workbench-submit').click()
            expect(page.locator('#lab-progress')).to_contain_text('completed',timeout=60000)
            expect(page.locator('#panel-items')).to_contain_text('验证：passed')
            page.get_by_role('button',name='查看验证报告',exact=True).click()
            expect(page.locator('#workbench-fields')).to_contain_text('holdout');page.locator('#workbench-close').click()
            page.get_by_role('button',name='核对单次轨迹与产物',exact=True).click()
            page.get_by_role('button',name='查看执行轨迹',exact=True).first.click()
            expect(page.locator('#workbench-fields')).to_contain_text('checks')
            page.get_by_role('button',name='查看 result.json',exact=True).first.click()
            expect(page.locator('#workbench-fields')).to_contain_text('total');page.locator('#workbench-close').click()
            page.get_by_role('button',name='采纳',exact=True).click();expect(page.locator('#panel-items')).to_contain_text('当前生效')
            page.get_by_role('button',name='版本记录',exact=True).click();expect(page.locator('#workbench-fields')).to_contain_text('applied');page.locator('#workbench-close').click()
            page.screenshot(path=str(output/'workbench.png'),full_page=True)
            page.get_by_role('button',name='撤回此版本',exact=True).click();expect(page.locator('#panel-items')).to_contain_text('revoked')
            assert not errors,errors
            browser.close()
            (output/'result.json').write_text(json.dumps({'passed':True,'browser':'Chromium','model':'scripted fixture',
                'checks':['settings save/apply/test','memory evidence','task execution','artifact preview/export','source snapshot','usage tree','reconnect/cancel','holdout verification','apply/revoke/version history'],'javascript_errors':errors},indent=2))
            print('PASS: real Chromium workbench acceptance with controlled model/HTTP fixtures')
    finally:
        server.terminate()
        try:server.wait(timeout=5)
        except subprocess.TimeoutExpired:server.kill();server.wait()
        log.close()


if __name__=='__main__':main()
