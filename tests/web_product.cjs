const {JSDOM} = require(process.env.NONOCODE_JSDOM_MODULE || 'jsdom');
const fs = require('fs');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2],'utf8');
const dom = new JSDOM(html,{url:'http://127.0.0.1:18888/',runScripts:'outside-only',pretendToBeVisual:true});
const window = dom.window;
const requests=[];
const replies={history:[{session_id:'saved',title:'History title',snippet:'Project details'}],
 runs:[{id:'task',session_id:'saved',prompt:'Read files',status:'completed',source:'tui',started_at:'today'}],
 run:{error:'',input_tokens:10,output_tokens:2,steps:[{tool_name:'ReadFile',status:'completed',output:'content'}]},
 memories:[{scope:'project',name:'language',description:'Language preference',body:'Use Chinese'}],
 memory_save:{},memory_forget:{},session_resume:{},session_rename:{},backup:{path:'/local/backup.db'}};
class Socket {
 static OPEN=1;
 constructor() {this.readyState=1; queueMicrotask(()=>this.onopen && this.onopen());}
 send(raw) {const packet=JSON.parse(raw); if(packet.type!=='product_request')return; requests.push(packet.data); queueMicrotask(()=>this.onmessage({data:JSON.stringify({type:'product_result',data:{request_id:packet.data.request_id,ok:true,result:replies[packet.data.action]}})}));}
 close(){}
}
window.WebSocket=Socket;
window.confirm=()=>true;
window.prompt=()=>'Renamed';
window.HTMLElement.prototype.scrollIntoView=()=>{};
window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
window.HTMLDialogElement.prototype.close=function(){this.open=false;};
window.marked={setOptions(){},parse(value){return value;}};
const inline=[...window.document.querySelectorAll('script')].find(script=>!script.src).textContent;
window.eval(inline);
const tick=()=>new Promise(resolve=>setTimeout(resolve,20));
(async()=>{
 await tick();
 const document=window.document;
 document.querySelector('[data-view="history"]').click(); await tick();
 assert.match(document.getElementById('panel-items').textContent,/History title/);
 assert.equal(document.getElementById('messages').hidden,true);
 document.querySelector('[data-view="runs"]').click(); await tick();
 assert.match(document.getElementById('panel-items').textContent,/Read files/);
 document.querySelector('.product-card button').click(); await tick();
 assert.match(document.getElementById('panel-items').textContent,/ReadFile.*completed/s);
 document.querySelector('[data-view="memories"]').click(); await tick();
 assert.match(document.getElementById('panel-items').textContent,/project\/language/);
 document.querySelector('.product-card button').click(); await tick();
 assert.equal(document.getElementById('memory-editor').open,true);
 document.getElementById('memory-body').value='Updated memory';
 document.getElementById('memory-form').dispatchEvent(new window.Event('submit',{cancelable:true})); await tick();
 assert.equal(requests.find(request=>request.action==='memory_save').body,'Updated memory');
 document.getElementById('new-memory').click(); await tick();
 document.getElementById('memory-name').value='new-memory';
 document.getElementById('memory-body').value='New memory';
 document.getElementById('memory-form').dispatchEvent(new window.Event('submit',{cancelable:true})); await tick();
 assert.equal(requests.filter(request=>request.action==='memory_save')[1].create,true);
 const malicious='<img src=x onerror=alert(1)><script>alert(1)</script><a href="javascript:alert(1)" onclick="alert(1)">bad</a><a href="https://example.com">safe</a><strong>bold</strong>';
 const cleaned=window.sanitizeMarkdown(malicious);
 assert(!/onerror|onclick|javascript:|<script|<img/.test(cleaned));
 assert(cleaned.includes('<strong>bold</strong>'));
 assert(cleaned.includes('https://example.com'));
 assert(requests.every(request=>request.request_id));
 console.log('PASS: history, task steps, memory editing/creation, request correlation, HTML sanitization');
 window.close();
})().catch(error=>{console.error(error);window.close();process.exitCode=1;});
