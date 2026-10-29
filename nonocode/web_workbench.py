"""Product workbench UI; all remote text is inserted as textContent."""
WORKBENCH_HTML = r'''
<dialog id="workbench-editor"><form id="workbench-form">
<h2 id="workbench-title"></h2><p id="workbench-help"></p><div id="workbench-fields"></div>
<button type="submit" id="workbench-submit">保存</button><button type="button" id="workbench-close">关闭</button>
</form></dialog>
'''
WORKBENCH_JS = r'''
const legacyRefreshProduct = refreshProduct;
const legacyShowProductView = showProductView;
let workbenchSubmit = null;
let labPoll = null;
let lastLabState = "";
let settingsDocument = null;
const productLabels = {artifacts:'产物',sources:'来源',usage:'用量与执行树',settings:'配置',evolution:'进化实验室'};
function textNode(tag,text,parent) { const el=document.createElement(tag);el.textContent=String(text ?? '');if(parent)parent.appendChild(el);return el; }
function field(parent,label,id,value='',type='text',choices=null) {
 const row=textNode('label',label,parent);let el;
 if(choices) { el=document.createElement('select');for(const [val,title] of choices) {const option=textNode('option',title,el);option.value=val;} }
 else if(type==='textarea') el=document.createElement('textarea');
 else {el=document.createElement('input');el.type=type;if(type==='number'){el.step='any';el.min='0';}}
 el.id=id;el.value=value ?? '';if(type==='checkbox')el.checked=!!value;
 row.appendChild(el);return el;
}
function editor(title,help,render,submit) {
 document.getElementById('workbench-title').textContent=title;document.getElementById('workbench-help').textContent=help;
 const fields=document.getElementById('workbench-fields');fields.replaceChildren();render(fields);workbenchSubmit=submit;
 document.getElementById('workbench-submit').hidden=!submit;
 document.getElementById('workbench-editor').showModal();
}
function previewText(title,text) { editor(title,'',parent=>textNode('pre',text,parent),null); }
function downloadPayload(result) {
 const data=Uint8Array.from(atob(result.base64),char=>char.charCodeAt(0));
 const url=URL.createObjectURL(new Blob([data],{type:result.media_type || 'application/octet-stream'}));
 const a=document.createElement('a');a.href=url;a.download=result.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function button(parent,title,action) {cardButton(parent,title,action);return parent.lastElementChild;}
function heading(list,title,detail) {const card=document.createElement('article');card.className='product-card';textNode('h3',title,card);textNode('p',detail,card);list.appendChild(card);return card;}
for(const [view,label] of Object.entries(productLabels)) {
 const button=document.createElement('button');button.dataset.view=view;button.textContent=label;button.onclick=()=>showProductView(view);
 document.getElementById('workspace-nav').insertBefore(button,document.getElementById('backup-db'));
}
showProductView=function(view) {
 if(labPoll) {clearInterval(labPoll);labPoll=null;}
 legacyShowProductView(view);
 const hints={artifacts:'文件与最终回答会自动归档。预览和下载使用当时的快照；版本对比不会修改原文件。',
 sources:'保留搜索摘要和实际读取的正文快照；抓取时间不是网页内容的发布时间。',
 usage:'汇总主代理、子代理及后台调用。金额为配置价格估算；未定价或未返回用量的调用单独标明。',
 settings:'配置保存到当前项目并应用于后续任务。密钥不回传；保存时会校验并检测其他窗口的修改。',
 evolution:'能力候选须通过 development 与 holdout 对照任务后才能采纳。替代版本撤回后恢复此前仍有效的规则。'};
 if(hints[view])document.getElementById('panel-hint').textContent=hints[view];
 document.getElementById('panel-query').disabled=['usage','settings','evolution','runs'].includes(view);
 if(view==='evolution')labPoll=setInterval(()=>refreshLabJob().catch(()=>{}),2000);
};
refreshProduct=async function() {
 const view=productView;
 if(!Object.hasOwn(productLabels,view))return legacyRefreshProduct();
 const query=document.getElementById('panel-query').value;
 const result=await productRequest(view,{query});if(view!==productView)return;
 const list=document.getElementById('panel-items');list.replaceChildren();
 if(view==='settings')return renderSettings(list,result);
 if(view==='usage')return renderUsage(list,result);
 if(view==='evolution')return renderEvolution(list,result);
 if(!result.length)textNode('p','暂无记录',list);
 for(const item of result) {
  if(view==='artifacts') {
   const card=heading(list,item.relative_path,`v${item.version} · ${item.size} bytes · ${item.created_at}`);
   button(card,'预览',async()=>{const data=await productRequest('artifact',{id:item.id});previewText(item.relative_path,data.text+(data.truncated?'\n（预览已截断）':''));});
   button(card,'下载',async()=>downloadPayload(await productRequest('artifact_download',{id:item.id})));
   button(card,'连同来源导出',async()=>downloadPayload(await productRequest('artifact_download',{id:item.id,bundle:true})));
   const versions=result.filter(other=>other.relative_path===item.relative_path && other.id!==item.id);
   if(versions.length)button(card,'比较版本',async()=>editor('比较 '+item.relative_path,'选择另一份归档快照',parent=>{
    field(parent,'比较到','artifact-compare','', 'text',versions.map(other=>[other.id,'v'+other.version+' · '+other.created_at]));
   },async()=>{const data=await productRequest('artifact_diff',{id:item.id,other_id:document.getElementById('artifact-compare').value});document.getElementById('workbench-editor').close();previewText('版本差异',data.text || '内容相同');}));
  } else {
   const card=heading(list,item.title || item.url,(item.kind==='page'?'已读取正文':'搜索摘要')+' · '+item.retrieved_at+'\n'+item.snippet);
   const link=textNode('a','打开原网页',card);try {const url=new URL(item.url);if(['http:','https:'].includes(url.protocol)){link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';}}catch(_){}
   button(card,'查看当时快照',async()=>{const source=await productRequest('source',{id:item.id});previewText(source.title,source.url+'\n抓取时间：'+source.retrieved_at+'\n\n'+source.text);});
  }
 }
};
function renderUsage(list,data) {
 heading(list,'调用汇总',`${data.total_tokens} tokens · 已知费用估算 $${Number(data.estimated_cost_usd).toFixed(6)} · ${data.unknown_cost_calls} 次调用费用未知${data.limited?'；下方仅显示最近记录':''}`);
 const tree=heading(list,'执行树','点击任务可核对该任务使用的记忆。');
 const roots=data.runs.filter(run=>!run.parent_run_id);
 function addRun(run,parent,depth=0) {
  if(depth>20)return;
  const row=heading(parent,'　'.repeat(depth)+(run.prompt || run.source),run.status+' · '+run.source+' · '+run.id);
  button(row,'查看记忆使用',async()=>{const receipts=await productRequest('memory_usage',{run_id:run.id});previewText('记忆使用记录',receipts.map(item=>item.scope+'/'+item.name+' · '+(item.kind==='body'?'读取正文':'注入索引')+' · '+item.created_at).join('\n') || '没有记录到记忆读取');});
  data.runs.filter(item=>item.parent_run_id===run.id).forEach(item=>addRun(item,parent,depth+1));
 }
 roots.forEach(run=>addRun(run,tree));
 for(const call of data.calls)heading(list,call.provider+' / '+call.model,`${call.phase} · ${call.status} · ${call.total_tokens} tokens · ${call.elapsed.toFixed(2)}s · ${call.cost_usd===null?'费用未知':'估算 $'+Number(call.cost_usd).toFixed(6)}\n根任务 ${call.root_run_id}`);
}
function renderSettings(list,documentData) {
 settingsDocument=documentData;
 const card=heading(list,'模型和执行配置',documentData.note+'\n'+(documentData.message || ''));
 const form=document.createElement('form');form.id='settings-form';card.appendChild(form);
 const values=structuredClone(documentData.values);let index=0;
 const providers=values.providers.length?values.providers:[{name:'default',protocol:'anthropic',base_url:'',model:'',api_key:'__KEEP__',thinking:false,max_output_tokens:4096,context_window:0,pricing:{}}];
 const select=field(form,'模型配置','settings-provider','0','text',providers.map((item,i)=>[String(i),item.name]));
 const providerFields=document.createElement('div');form.appendChild(providerFields);
 function renderProvider() {
  const provider=providers[index];providerFields.replaceChildren();
  field(providerFields,'名称','settings-name',provider.name);
  field(providerFields,'接口协议','settings-protocol',provider.protocol,'text',[['anthropic','Anthropic 兼容'],['openai','OpenAI Responses'],['openai-compat','OpenAI Chat Completions']]);
  field(providerFields,'API 地址','settings-base-url',provider.base_url,'url');
  field(providerFields,'模型 ID','settings-model',provider.model);
  const key=field(providerFields,'API 密钥（留空保留现值）','settings-key','','password');key.autocomplete='new-password';
  textNode('p',provider.api_key_present?'已有可用密钥；不会回传密钥内容':'未检测到密钥，可填写或使用环境变量',providerFields);
  field(providerFields,'清空配置内密钥，改用环境变量','settings-clear-key',false,'checkbox');
  field(providerFields,'启用思考','settings-thinking',provider.thinking,'checkbox');
  field(providerFields,'最大输出 tokens','settings-output',provider.max_output_tokens || 4096,'number');
  field(providerFields,'上下文窗口（0 自动）','settings-context',provider.context_window || 0,'number');
  const price=provider.pricing || {};
  for(const [name,label] of [['input_per_million','输入价格 / 百万 tokens（美元）'],['output_per_million','输出价格 / 百万 tokens（美元）'],['cache_read_per_million','缓存读取价格（可选）'],['cache_creation_per_million','缓存写入价格（可选）']])field(providerFields,label,'price-'+name,price[name] ?? '','number');
  field(providerFields,'价格记录日期','price-as-of',price.as_of || '','date');
 }
 function readProvider() {
  const previous=providers[index],pricing={};
  for(const name of ['input_per_million','output_per_million','cache_read_per_million','cache_creation_per_million']) {const raw=document.getElementById('price-'+name).value;if(raw!=='')pricing[name]=Number(raw);}
  if(Object.keys(pricing).length)pricing.as_of=document.getElementById('price-as-of').value;
  providers[index]={...previous,name:document.getElementById('settings-name').value,protocol:document.getElementById('settings-protocol').value,
   base_url:document.getElementById('settings-base-url').value,model:document.getElementById('settings-model').value,
   api_key:document.getElementById('settings-clear-key').checked?'':document.getElementById('settings-key').value || '__KEEP__',
   thinking:document.getElementById('settings-thinking').checked,max_output_tokens:Number(document.getElementById('settings-output').value),context_window:Number(document.getElementById('settings-context').value),pricing};
 }
 select.onchange=()=>{readProvider();index=Number(select.value);renderProvider();};renderProvider();
 const add=document.createElement('button');add.type='button';add.textContent='添加模型配置';add.onclick=()=>{readProvider();providers.push({name:'model-'+providers.length,protocol:'anthropic',base_url:'',model:'',api_key:'__KEEP__',pricing:{}});const option=textNode('option',providers.at(-1).name,select);option.value=String(providers.length-1);select.value=option.value;index=providers.length-1;renderProvider();};form.appendChild(add);
 textNode('p','第一个配置为默认模型。当前默认：'+providers[0].name,form);
 button(form,'设当前模型为默认',async()=>{readProvider();const selected=providers[index];providers.splice(index,1);providers.unshift(selected);index=0;select.replaceChildren();providers.forEach((item,i)=>{const option=textNode('option',item.name,select);option.value=String(i);});select.value='0';renderProvider();notice('已设为表单默认，保存后生效');});
 field(form,'权限模式','settings-permission',values.permission_mode,'text',[['default','默认：读取放行，修改确认'],['acceptEdits','允许文件修改'],['plan','规划模式'],['bypassPermissions','自动执行（保留危险命令拦截）']]);
 for(const [name,value] of Object.entries(values.run_limits))field(form,name==='budget_timezone'?'每日预算时区':name+'（0 关闭）','limit-'+name,value,name==='budget_timezone'?'text':'number');
 field(form,'启用内置联网','settings-web-enabled',values.web.enabled,'checkbox');
 field(form,'搜索服务','settings-search',values.web.search_provider,'text',[['bing','Bing 公开 RSS'],['brave','Brave API']]);
 field(form,'搜索密钥环境变量','settings-search-env',values.web.search_api_key_env || 'BRAVE_SEARCH_API_KEY');
 function payload() {
  readProvider();const limits={};for(const name of Object.keys(values.run_limits))limits[name]=name==='budget_timezone'?document.getElementById('limit-'+name).value:Number(document.getElementById('limit-'+name).value);
  return {providers,permission_mode:document.getElementById('settings-permission').value,run_limits:limits,
   web:{enabled:document.getElementById('settings-web-enabled').checked,search_provider:document.getElementById('settings-search').value,search_api_key_env:document.getElementById('settings-search-env').value}};
 }
 button(form,'测试当前模型连接',async()=>{const config=payload();config.providers=[config.providers[index]];const result=await productRequest('settings_test',{values:config});notice(result.message);});
 const save=textNode('button','保存并应用',form);save.type='submit';save.id='settings-save';
 form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try {const result=await productRequest('settings_save',{values:payload(),revision:documentData.revision});notice(result.message);renderSettingsAfterSave(result);}catch(error){notice(error.message);}finally{save.disabled=false;}};
 function renderSettingsAfterSave(result){list.replaceChildren();renderSettings(list,result);}
 const details=textNode('details','',card);textNode('summary','查看配置来源',details);textNode('pre',JSON.stringify(documentData.field_sources,null,2),details);
}
async function refreshLabJob() {
 if(productView!=='evolution')return;
 const job=await productRequest('evolution_job');let status=document.getElementById('lab-progress');
 if(!status)return;status.textContent=job.status?job.status+' · '+job.progress:'尚未启动验证';
 const cancel=document.getElementById('lab-cancel');if(cancel)cancel.disabled=job.status!=='running';
 const state=(job.id || '')+':'+(job.status || '');const changed=lastLabState!==state;lastLabState=state;
 if(changed && ['completed','failed','cancelled'].includes(job.status))await refreshProduct();
}
function openTaskEditor(spec=null,observed='') {
 const value=spec || {id:'my-task',prompt:'描述任务',inputs:[],checks:[],split:'development',repetitions:2,timeout_seconds:60,max_iterations:8};
 editor('登记冻结测试任务','填写工作区内的输入路径和可执行检查。至少有一个内容检查，例如 {"type":"text_contains","path":"result.txt","text":"期望内容"}。'+(observed?'\n原执行问题：'+observed:''),parent=>{
  const textarea=field(parent,'任务 JSON','task-spec',JSON.stringify(value,null,2),'textarea');textarea.rows=18;
 },async()=>{await productRequest('evolution_task_save',{spec:JSON.parse(document.getElementById('task-spec').value)});notice('任务和输入快照已冻结');document.getElementById('workbench-editor').close();await refreshProduct();});
}
function openCandidateEditor(item=null) {
 editor(item?'提出替代版本':'新增改进候选','新增候选保持待审核。能力规则须完成 development 与 holdout 对照验证。',parent=>{
  field(parent,'改进规则','candidate-rule',item?.feedback || '','textarea');
  field(parent,'类别','candidate-kind',item?.kind || 'capability','text',[['capability','能力改进：需要执行验证'],['preference','表达偏好：用户审核']]);
  field(parent,'适用场景','candidate-cue',item?.cue || '');field(parent,'用户依据或修改说明','candidate-evidence','','textarea');
 },async()=>{await productRequest('evolution_create',{rule:document.getElementById('candidate-rule').value,kind:document.getElementById('candidate-kind').value,
  cue:document.getElementById('candidate-cue').value,evidence:document.getElementById('candidate-evidence').value,replaces_id:item?.id || ''});
  document.getElementById('workbench-editor').close();notice('候选已保存');await refreshProduct();});
}
async function renderEvolution(list,items) {
 const tools=heading(list,'候选、固定任务与验证','当前版本保留为基线；候选通过检查并持续改善后才允许采纳。');
 button(tools,'新增候选',async()=>openCandidateEditor());button(tools,'登记测试任务',async()=>openTaskEditor());
 textNode('p','尚未启动验证',tools).id='lab-progress';const cancel=button(tools,'取消当前验证',async()=>{await productRequest('evolution_cancel');notice('已请求取消');});cancel.id='lab-cancel';
 const tasks=await productRequest('evolution_tasks');if(productView!=='evolution')return;
 for(const task of tasks) {
  const card=heading(list,task.spec.id+' · '+(task.spec.split || 'development'),task.spec.checks.length+' 项检查 · '+task.spec.repetitions+' 轮对照 · 快照 '+task.fingerprint.slice(0,12));
  button(card,'查看或登记新版本',async()=>openTaskEditor(task.spec));
 }
 for(const item of items) {
  const card=heading(list,item.feedback,item.kind+' · '+item.status+(item.effective?' · 当前生效':'')+'\n场景：'+(item.cue || '未指定')+'\n证据：'+(item.evidence || '手动添加')+'\n验证：'+(item.verification?.outcome || '未运行'));
  button(card,'提出修改',async()=>openCandidateEditor(item));
  if(item.status==='pending' && item.kind==='capability')button(card,'运行对照验证',async()=>editor('选择验证任务','最多 3 个，须包含 development 和 holdout。保留任务仅用于执行检验。',parent=>{
   for(const task of tasks)field(parent,task.spec.id+' · '+(task.spec.split || 'development'),'task-choice-'+task.spec.id,false,'checkbox');
  },async()=>{const selected=tasks.filter(task=>document.getElementById('task-choice-'+task.spec.id).checked).map(task=>task.spec.id);const job=await productRequest('evolution_verify',{id:item.id,task_ids:selected});notice(job.progress);document.getElementById('workbench-editor').close();await refreshLabJob();}));
  if(item.status==='pending')for(const [action,label] of [['apply','采纳'],['reject','拒绝']])button(card,label,async()=>{await productRequest('evolution_transition',{id:item.id,transition:action});notice(action==='apply'?'已采纳':'已拒绝');await refreshProduct();});
  if(item.status==='applied')button(card,'撤回此版本',async()=>{await productRequest('evolution_transition',{id:item.id,transition:'revoke'});notice('已撤回，之前仍获认可的版本恢复使用');await refreshProduct();});
  if(item.verification)button(card,'查看验证报告',async()=>{const report=await productRequest('evolution_report',{id:item.id});previewText('对照验证报告',report.reason+'\n'+report.scope_note+'\n输入 '+report.input_tokens+' / 输出 '+report.output_tokens+' tokens\n'+report.runs.map(run=>`${run.task_id} ${run.split || 'development'} #${run.repeat+1} ${run.arm} ${run.checks.filter(check=>check.passed).length}/${run.checks.length} · ${(run.elapsed || 0).toFixed(2)}s${run.error?' · '+run.error:''}`).join('\n'));});
  if(item.verification)button(card,'核对单次轨迹与产物',async()=>{const report=await productRequest('evolution_report',{id:item.id});editor('逐次执行证据','可以核对模型收到的工具结果、固定检查和实际生成文件。',parent=>{
   report.runs.forEach((run,index)=>{const row=heading(parent,`${run.task_id} #${run.repeat+1} ${run.arm}`,`${run.elapsed?.toFixed(2) || '?'}s · ${run.estimated_cost_usd==null?'费用未知':'估算 $'+run.estimated_cost_usd}`);
    button(row,'查看执行轨迹',async()=>{const trial=await productRequest('evolution_trial',{id:item.id,index});const pre=row.querySelector('pre') || textNode('pre','',row);pre.textContent=JSON.stringify({checks:trial.checks,trace:trial.trace},null,2);});
    for(const artifact_path of run.artifact_paths || [])button(row,'查看 '+artifact_path,async()=>{const trial=await productRequest('evolution_trial',{id:item.id,index,artifact_path});const pre=row.querySelector('pre') || textNode('pre','',row);pre.textContent=trial.text;});
   });
  },null);});
  button(card,'版本记录',async()=>{const versions=await productRequest('evolution_versions',{id:item.id});editor('版本记录','恢复历史内容会生成新的待审核候选，能力规则仍须重新验证。',parent=>{
   for(const version of versions){const row=heading(parent,version.created_at,version.after.feedback+' · '+version.after.status);textNode('pre',JSON.stringify({before:version.before,after:version.after},null,2),row);button(row,'恢复为候选',async()=>{await productRequest('evolution_restore',{id:item.id,version_id:version.id});document.getElementById('workbench-editor').close();await refreshProduct();});}
  },null);});
 }
 await refreshLabJob();
}
document.getElementById('workbench-close').onclick=()=>document.getElementById('workbench-editor').close();
document.getElementById('workbench-form').onsubmit=async event=>{event.preventDefault();const submit=document.getElementById('workbench-submit');submit.disabled=true;try{if(workbenchSubmit)await workbenchSubmit();}catch(error){notice(error.message);}finally{submit.disabled=false;}};
// Extend existing cards with task-specific memory receipts and case creation.
const legacyCardRefresh=legacyRefreshProduct;
const baseRefresh=refreshProduct;
refreshProduct=async function() {
 await baseRefresh();
 if(productView==='runs') {
  const rows=await productRequest('runs');const cards=[...document.querySelectorAll('#panel-items .product-card')];
  rows.forEach((item,index)=>{const card=cards[index];if(!card)return;
   button(card,'记忆使用',async()=>{const receipts=await productRequest('memory_usage',{run_id:item.id});previewText('此次任务使用的记忆',receipts.map(row=>row.scope+'/'+row.name+' · '+(row.kind==='body'?'读取正文':'注入索引')+' · '+row.created_at).join('\n') || '未记录到记忆使用');});
   if(['failed','cancelled','interrupted'].includes(item.status))button(card,'整理为测试草稿',async()=>{const draft=await productRequest('evolution_draft',{run_id:item.id});openTaskEditor(draft.spec,draft.observed_failure);});
  });
 }
 if(productView==='memories') {
  const rows=await productRequest('memories',{query:document.getElementById('panel-query').value});const cards=[...document.querySelectorAll('#panel-items .product-card')];
  rows.forEach((item,index)=>{if(!cards[index])return;textNode('p','依据：'+(item.evidence || '旧记忆未记录原始依据')+'\n来源：'+({manual:'用户编辑确认',conversation_extraction:'对话自动提取',legacy:'历史记忆，未记录原始依据'}[item.source] || item.source)+' · '+(item.active?'参与检索':'已停用或过期')+'\n最近使用：'+(item.last_used_at || '未记录')+(item.conflicts?.length?'\n同主题存在不同内容：'+item.conflicts.join('、'):''),cards[index]);});
 }
};
const oldOpenMemoryEditor=openMemoryEditor;
openMemoryEditor=function(item=null) {
 oldOpenMemoryEditor(item);
 document.getElementById('memory-evidence').value=item?.evidence || '';
 document.getElementById('memory-expires').value=item?.expires_at || '';
 document.getElementById('memory-topic').value=item?.topic || '';
 document.getElementById('memory-enabled').checked=item?.enabled!==false;
};
document.getElementById('memory-form').onsubmit=async event=>{
 event.preventDefault();
 const fields={scope:document.getElementById('memory-scope').value,name:document.getElementById('memory-name').value,body:document.getElementById('memory-body').value,create:creatingMemory,
  evidence:document.getElementById('memory-evidence').value,expires_at:document.getElementById('memory-expires').value,topic:document.getElementById('memory-topic').value,enabled:document.getElementById('memory-enabled').checked};
 try{await productRequest('memory_save',fields);document.getElementById('memory-editor').close();notice('记忆已保存');await refreshProduct();}catch(error){notice(error.message);}
};
'''
