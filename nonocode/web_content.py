# Remote 模式的 Web 前端 HTML。
# Web UI 静态 HTML。

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NoNocode Remote</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
:root {
  --bg: #1a1b26;
  --bg-surface: #24283b;
  --bg-input: #1f2335;
  --text: #c0caf5;
  --text-dim: #565f89;
  --text-bright: #e0e6ff;
  --accent: #bb9af7;
  --accent-dim: #7c6da4;
  --green: #9ece6a;
  --red: #f7768e;
  --yellow: #e0af68;
  --blue: #7aa2f7;
  --border: #3b4261;
  --tool-bg: #1f2335;
  --code-bg: #16161e;
}

html, body { height: 100%; background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', Consolas, monospace; font-size: 14px; }

#app { display: flex; flex-direction: column; height: 100vh; max-width: 960px; margin: 0 auto; }

/* 顶部状态栏 */
#status-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 16px; border-bottom: 1px solid var(--border);
  font-size: 12px; color: var(--text-dim); flex-shrink: 0;
}
#status-bar .brand { color: var(--accent); font-weight: bold; font-size: 14px; }
#status-bar .info { display: flex; gap: 16px; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.dot.connected { background: var(--green); }
.dot.disconnected { background: var(--red); }

/* 消息区域 */
#messages {
  flex: 1; overflow-y: auto; padding: 16px;
  scroll-behavior: smooth;
}
#messages::-webkit-scrollbar { width: 6px; }
#messages::-webkit-scrollbar-track { background: transparent; }
#messages::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

.msg { margin-bottom: 16px; line-height: 1.6; }
.msg-user { }
.msg-user .label { color: var(--accent); font-weight: bold; }
.msg-user .content { color: var(--text-bright); margin-top: 4px; white-space: pre-wrap; }
.msg-assistant .content { color: var(--text); margin-top: 4px; }
.msg-assistant .content p { margin-bottom: 8px; }
.msg-assistant .content code {
  background: var(--code-bg); padding: 2px 6px; border-radius: 4px;
  font-size: 13px;
}
.msg-assistant .content pre {
  background: var(--code-bg); padding: 12px; border-radius: 6px;
  margin: 8px 0; overflow-x: auto; border: 1px solid var(--border);
}
.msg-assistant .content pre code { background: none; padding: 0; }

.msg-system { color: var(--text-dim); font-size: 13px; white-space: pre-wrap; }
.msg-error { color: var(--red); }

/* 工具调用 */
.tool-block {
  background: var(--tool-bg); border: 1px solid var(--border); border-radius: 6px;
  margin: 8px 0; overflow: hidden;
}
.tool-header {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; cursor: pointer; user-select: none;
  font-size: 13px;
}
.tool-header:hover { background: rgba(255,255,255,0.03); }
.tool-header .icon { font-size: 12px; color: var(--text-dim); transition: transform 0.2s; }
.tool-header .icon.expanded { transform: rotate(90deg); }
.tool-header .name { color: var(--blue); font-weight: 600; }
.tool-header .status { margin-left: auto; font-size: 12px; }
.tool-header .status.ok { color: var(--green); }
.tool-header .status.err { color: var(--red); }
.tool-header .status.loading { color: var(--yellow); }
.tool-body {
  display: none; padding: 8px 12px; border-top: 1px solid var(--border);
  font-size: 12px; max-height: 300px; overflow-y: auto; white-space: pre-wrap;
  color: var(--text-dim);
}
.tool-body.show { display: block; }

/* 思考过程（折叠） */
.thinking-block {
  background: var(--tool-bg); border: 1px solid var(--border); border-radius: 6px;
  margin: 8px 0; overflow: hidden;
}
.thinking-header {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; cursor: pointer; user-select: none;
  font-size: 13px; color: var(--text-dim);
}
.thinking-header:hover { background: rgba(255,255,255,0.03); }
.thinking-header .icon { font-size: 12px; transition: transform 0.2s; }
.thinking-header .icon.expanded { transform: rotate(90deg); }
.thinking-body {
  display: none; padding: 8px 12px; border-top: 1px solid var(--border);
  font-size: 12px; max-height: 300px; overflow-y: auto; white-space: pre-wrap;
  color: var(--text-dim); font-style: italic;
}
.thinking-body.show { display: block; }

/* 权限弹窗 */
.perm-dialog {
  background: var(--bg-surface); border: 2px solid var(--yellow);
  border-radius: 8px; padding: 16px; margin: 12px 0;
}
.perm-dialog .title { color: var(--yellow); font-weight: bold; margin-bottom: 8px; }
.perm-dialog .desc { color: var(--text); margin-bottom: 12px; font-size: 13px; white-space: pre-wrap; }
.perm-dialog .actions { display: flex; gap: 8px; }
.perm-dialog button {
  padding: 6px 16px; border-radius: 4px; border: 1px solid var(--border);
  cursor: pointer; font-family: inherit; font-size: 13px;
}
.btn-allow { background: var(--green); color: var(--bg); border-color: var(--green); }
.btn-deny { background: transparent; color: var(--red); border-color: var(--red); }
.btn-always { background: transparent; color: var(--blue); border-color: var(--blue); }

/* 斜杠命令菜单 */
#slash-menu {
  display: none; position: absolute; bottom: 100%; left: 0; right: 0;
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: 6px; margin-bottom: 4px; max-height: 240px;
  overflow-y: auto; box-shadow: 0 -4px 12px rgba(0,0,0,0.3);
}
#slash-menu.show { display: block; }
.slash-item {
  padding: 8px 12px; cursor: pointer; display: flex; gap: 8px; align-items: baseline;
}
.slash-item:hover, .slash-item.active { background: rgba(187,154,247,0.1); }
.slash-item .cmd-name { color: var(--accent); font-weight: 600; white-space: nowrap; }
.slash-item .cmd-desc { color: var(--text-dim); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 输入区域 */
#input-area {
  flex-shrink: 0; border-top: 1px solid var(--border);
  padding: 12px 16px; display: flex; gap: 8px; align-items: flex-end;
}
#input-area textarea {
  flex: 1; background: var(--bg-input); color: var(--text-bright);
  border: 1px solid var(--border); border-radius: 6px;
  padding: 10px 12px; font-family: inherit; font-size: 14px;
  resize: none; outline: none; min-height: 42px; max-height: 200px;
  line-height: 1.5;
}
#input-area textarea:focus { border-color: var(--accent); }
#input-area textarea::placeholder { color: var(--text-dim); }
#send-btn {
  background: var(--accent); color: var(--bg); border: none;
  border-radius: 6px; padding: 10px 16px; cursor: pointer;
  font-family: inherit; font-size: 14px; font-weight: 600;
  white-space: nowrap;
}
#send-btn:hover { opacity: 0.9; }
#send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* 流式光标闪烁 */
.cursor { animation: blink 1s step-end infinite; }
@keyframes blink { 50% { opacity: 0; } }

/* 完成指示 */
.done-indicator { color: var(--text-dim); font-size: 12px; margin-top: 4px; }

/* Markdown 列表 */
.msg-assistant .content ul, .msg-assistant .content ol { padding-left: 20px; margin: 8px 0; }
.msg-assistant .content li { margin: 4px 0; }
.msg-assistant .content h1, .msg-assistant .content h2, .msg-assistant .content h3 {
  color: var(--text-bright); margin: 12px 0 8px; }
.msg-assistant .content blockquote {
  border-left: 3px solid var(--accent-dim); padding-left: 12px;
  color: var(--text-dim); margin: 8px 0;
}
.msg-assistant .content table { border-collapse: collapse; margin: 8px 0; }
.msg-assistant .content th, .msg-assistant .content td {
  border: 1px solid var(--border); padding: 6px 12px; text-align: left; }
.msg-assistant .content th { background: var(--bg-surface); }

[hidden] { display:none !important; }
#workspace-nav {display:flex; gap:8px; padding:10px 16px; border-bottom:1px solid var(--border); flex-wrap:wrap; align-items:center;}
#workspace-nav button,.product-card button,#panel-search button,dialog button {background:var(--bg-surface);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:7px 12px;cursor:pointer;}
#workspace-nav button.active {color:var(--accent);border-color:var(--accent);}
#product-notice {font-size:12px;color:var(--text-dim);overflow-wrap:anywhere;}
#workspace-panel {flex:1;overflow:auto;padding:20px;}
#panel-search {display:flex;gap:8px;flex-wrap:wrap;}
#panel-query {flex:1;min-width:160px;}
#panel-hint {color:var(--text-dim);margin:12px 0;}
.product-card {border:1px solid var(--border);border-radius:10px;padding:16px;margin:12px 0;background:var(--bg-surface);overflow-wrap:anywhere;}
.product-card p {white-space:pre-wrap;color:var(--text-dim);margin:10px 0;}
.product-card button {margin:4px 8px 0 0;}
.product-card pre {white-space:pre-wrap;max-height:360px;overflow:auto;margin-top:12px;}
dialog {margin:auto;background:var(--bg-surface);color:var(--text);border:1px solid var(--border);border-radius:12px;width:min(640px,95vw);padding:24px;}
dialog::backdrop {background:#0009;}
dialog label {display:block;margin:14px 0;}
dialog textarea {display:block;width:100%;resize:vertical;}
dialog input,dialog select,dialog textarea,#panel-query {padding:9px;background:var(--bg-input);color:var(--text);border:1px solid var(--border);border-radius:5px;font:inherit;}
</style>
</head>
<body>
<div id="app">
  <div id="status-bar">
    <span class="brand">⚡ NoNocode Remote</span>
    <div class="info">
      <span id="conn-status"><span class="dot disconnected"></span>Connecting...</span>
      <span id="token-info"></span>
    </div>
  </div>
  <nav id="workspace-nav" aria-label="工作区">
    <button data-view="chat" class="active">对话</button><button data-view="history">历史</button>
    <button data-view="runs">任务中心</button><button data-view="memories">记忆</button>
    <button id="backup-db">备份数据</button><span id="product-notice" role="status"></span>
  </nav>
  <section id="workspace-panel" hidden>
    <form id="panel-search"><input id="panel-query" aria-label="搜索关键词" placeholder="搜索历史对话或记忆"><button>搜索 / 刷新</button><button type="button" id="new-memory" hidden>添加记忆</button></form>
    <p id="panel-hint"></p><div id="panel-items"></div>
  </section>
  <dialog id="memory-editor">
    <form id="memory-form"><h2>编辑记忆</h2>
      <label>范围 <select id="memory-scope"><option value="project">当前项目</option><option value="user">所有项目</option></select></label>
      <label>名称 <input id="memory-name" required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxlength="100" placeholder="例如 preferred-language"></label>
      <label>内容 <textarea id="memory-body" rows="10" required maxlength="100000"></textarea></label>
      <p>保存后会用于后续对话。名称使用小写英文、数字和短横线。</p>
      <button type="submit">保存记忆</button><button type="button" id="memory-close">取消</button>
    </form>
  </dialog>

  <div id="messages"></div>
  <div id="input-area" style="position:relative;">
    <div id="slash-menu"></div>
    <textarea id="input" placeholder="Send a message... (Enter to send, Shift+Enter for newline)" rows="1"></textarea>
    <button id="send-btn">Send</button>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>
const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('send-btn');
const connStatus = document.getElementById('conn-status');
const tokenInfo = document.getElementById('token-info');
const slashMenu = document.getElementById('slash-menu');

let ws = null;
let streaming = false;
let allCommands = [];
let slashCursor = 0;
let slashFiltered = [];
let currentAssistantEl = null;
let currentStreamText = '';
let currentThinkingEl = null;
let currentThinkingText = '';
let autoScroll = true;
let pingTimer = null;
let connectedOnce = false;

// Markdown 渲染配置
if (typeof marked !== 'undefined') {
  marked.setOptions({ breaks: true, gfm: true });
}

function renderMarkdown(text) {
  if (typeof marked !== 'undefined') {
    try { return sanitizeMarkdown(marked.parse(text)); } catch(e) {}
  }
  return escapeHtml(text);
}

function sanitizeMarkdown(html) {
  const documentNode = new DOMParser().parseFromString(html,'text/html');
  const allowed = new Set(['P','BR','STRONG','EM','DEL','CODE','PRE','BLOCKQUOTE','UL','OL','LI','H1','H2','H3','H4','H5','H6','HR','TABLE','THEAD','TBODY','TR','TH','TD','A']);
  function clean(node) {
    if (node.nodeType === 3) return document.createTextNode(node.textContent);
    const fragment = document.createDocumentFragment();
    if (node.nodeType !== 1 || ['SCRIPT','STYLE','IFRAME','OBJECT','EMBED','SVG','MATH'].includes(node.tagName)) return fragment;
    const output = allowed.has(node.tagName) ? document.createElement(node.tagName.toLowerCase()) : fragment;
    if (node.tagName === 'A') {
      try {
        const url = new URL(node.getAttribute('href') || '',location.href);
        if (['http:','https:','mailto:'].includes(url.protocol)) { output.href=url.href; output.rel='noopener noreferrer'; }
      } catch(error) {}
    }
    for (const child of node.childNodes) output.appendChild(clean(child));
    return output;
  }
  const result = document.createElement('div');
  for (const node of documentNode.body.childNodes) result.appendChild(clean(node));
  return result.innerHTML;
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function connect() {
  if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
  if (ws) { try { ws.onclose = null; ws.close(); } catch(e) {} ws = null; }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');

  ws.onopen = () => {
    connStatus.innerHTML = '<span class="dot connected"></span>Connected';
    // 每 10 秒发一次应用层 ping，防止连接被中间件/浏览器回收
    pingTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping', data: {} }));
      }
    }, 10000);
  };

  ws.onclose = () => {
    connStatus.innerHTML = '<span class="dot disconnected"></span>Reconnecting...';
    if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
    pendingProduct.forEach(request => { clearTimeout(request.timer); request.reject(new Error('连接已断开，请重试')); });
    pendingProduct.clear();
    setTimeout(connect, 3000);
  };

  ws.onerror = () => {};

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    handleMessage(msg);
  };
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'product_result': productReply(msg.data); break;

    case 'state_sync': {
      messagesEl.innerHTML = '';
      currentAssistantEl = null;
      currentStreamText = '';
      currentThinkingEl = null;
      currentThinkingText = '';
      Object.keys(toolElements).forEach(key => delete toolElements[key]);
      for (const event of (msg.data.history || [])) handleMessage(event);
      for (const event of (msg.data.events || [])) handleMessage(event);
      for (const permission of (msg.data.permissions || [])) addPermissionDialog(permission);
      if (msg.data.truncated) addSystem('部分输出超过重连缓存上限；完整工具记录请查看任务详情。');
      streaming = !!msg.data.streaming;
      if (!streaming) finishPendingUI();
      updateUI();
      scrollToBottom();
      break;
    }

    case 'run_state':
      streaming = !!msg.data.streaming;
      if (!streaming) finishPendingUI();
      updateUI();
      break;

    case 'connected':
      if (!connectedOnce) {
        addSystem('Session: ' + msg.data.session + ' | CWD: ' + msg.data.cwd);
        connectedOnce = true;
      }
      break;

    case 'commands':
      allCommands = msg.data || [];
      break;

    case 'system':
      addSystem(msg.data.message);
      break;

    case 'clear':
      finishPendingUI();
      messagesEl.innerHTML = '';
      currentAssistantEl = null; currentStreamText = '';
      currentThinkingEl = null; currentThinkingText = '';
      Object.keys(toolElements).forEach(key => delete toolElements[key]);
      addSystem('Conversation cleared.');
      break;

    case 'command_done':
      streaming = false;
      updateUI();
      break;

    case 'replay_user':
      addUser(msg.data.content);
      break;

    case 'replay_assistant': {
      const el = addAssistant('');
      updateAssistant(el, msg.data.content, false);
      break;
    }

    case 'stream_text':
      if (currentThinkingEl) {
        currentThinkingEl.querySelector('.thinking-header span:last-child').textContent = '💭 Thought';
        currentThinkingEl = null;
        currentThinkingText = '';
      }
      if (!currentAssistantEl) {
        currentAssistantEl = addAssistant('');
        currentStreamText = '';
      }
      currentStreamText += msg.data.text;
      updateAssistant(currentAssistantEl, currentStreamText, true);
      break;

    case 'stream_end':
      if (currentAssistantEl) {
        updateAssistant(currentAssistantEl, currentStreamText, false);
      }
      currentAssistantEl = null;
      currentStreamText = '';
      break;

    case 'thinking_text':
      if (!currentThinkingEl) {
        currentThinkingEl = addThinking();
        currentThinkingText = '';
      }
      currentThinkingText += msg.data.text;
      currentThinkingEl.querySelector('.thinking-body').textContent = currentThinkingText;
      break;

    case 'tool_use':
      if (currentThinkingEl) {
        currentThinkingEl.querySelector('.thinking-header span:last-child').textContent = '💭 Thought';
        currentThinkingEl = null;
        currentThinkingText = '';
      }
      addToolUse(msg.data);
      break;

    case 'tool_result':
      updateToolResult(msg.data);
      break;

    case 'permission_request':
      addPermissionDialog(msg.data);
      break;

    case 'ask_user':
      addAskUserDialog(msg.data);
      break;

    case 'turn_complete':
      break;

    case 'loop_complete':
      if (currentAssistantEl) {
        updateAssistant(currentAssistantEl, currentStreamText, false);
        currentAssistantEl = null;
        currentStreamText = '';
      }
      currentThinkingEl = null;
      currentThinkingText = '';
      const el = document.createElement('div');
      el.className = 'done-indicator';
      el.textContent = '✻ Done in ' + msg.data.elapsed.toFixed(1) + 's';
      messagesEl.appendChild(el);
      streaming = false;
      updateUI();
      scrollToBottom();
      break;

    case 'usage':
      tokenInfo.textContent = 'In: ' + formatTokens(msg.data.inputTokens) + ' | Out: ' + formatTokens(msg.data.outputTokens);
      break;

    case 'error':
      addError(msg.data.message);
      streaming = false;
      updateUI();
      break;

    case 'compact':
      addSystem('⟳ ' + msg.data.message);
      break;

    case 'retry':
      addSystem('↻ Retrying: ' + msg.data.reason);
      break;
  }
}

function finishPendingUI() {
  if (currentAssistantEl) updateAssistant(currentAssistantEl, currentStreamText, false);
  currentAssistantEl = null; currentStreamText = '';
  currentThinkingEl = null; currentThinkingText = '';
  document.querySelectorAll('.perm-dialog .actions').forEach(el => el.remove());
  for (const key of Object.keys(toolElements)) {
    const block = toolElements[key];
    const status = block.querySelector('.status');
    if (status) { status.textContent = '已停止'; status.className = 'status err'; }
    delete toolElements[key];
  }
}

function formatTokens(n) {
  if (n > 1000000) return (n/1000000).toFixed(1) + 'M';
  if (n > 1000) return (n/1000).toFixed(1) + 'K';
  return '' + n;
}

function addUser(text) {
  const div = document.createElement('div');
  div.className = 'msg msg-user';
  div.innerHTML = '<span class="label">❯ </span><span class="content">' + escapeHtml(text) + '</span>';
  messagesEl.appendChild(div);
  scrollToBottom();
}

function addAssistant(text) {
  const div = document.createElement('div');
  div.className = 'msg msg-assistant';
  div.innerHTML = '<div class="content"></div>';
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function updateAssistant(el, text, isStreaming) {
  const contentEl = el.querySelector('.content');
  // 检测 <think>...</think> 标签，分离思考内容和正文
  const thinkMatch = text.match(/^<think>([\s\S]*?)<\/think>\s*([\s\S]*)$/);
  if (thinkMatch) {
    const thinkText = thinkMatch[1].trim();
    const bodyText = thinkMatch[2].trim();
    let html = '';
    if (thinkText) {
      html += '<div class="thinking-block">' +
        '<div class="thinking-header" onclick="toggleThinking(this)">' +
          '<span class="icon">▶</span>' +
          '<span>💭 Thought</span>' +
        '</div>' +
        '<div class="thinking-body">' + escapeHtml(thinkText) + '</div>' +
      '</div>';
    }
    if (bodyText) {
      html += renderMarkdown(bodyText);
    }
    if (isStreaming) html += '<span class="cursor">▎</span>';
    contentEl.innerHTML = html;
  } else if (text.startsWith('<think>') && isStreaming) {
    // 思考还没结束，显示为折叠的 thinking 块 + 光标
    const thinkBody = text.replace(/^<think>\s*/, '');
    let html = '<div class="thinking-block">' +
      '<div class="thinking-header" onclick="toggleThinking(this)">' +
        '<span class="icon">▶</span>' +
        '<span>💭 Thinking...</span>' +
      '</div>' +
      '<div class="thinking-body">' + escapeHtml(thinkBody) + '</div>' +
    '</div>';
    html += '<span class="cursor">▎</span>';
    contentEl.innerHTML = html;
  } else {
    let html = renderMarkdown(text);
    if (isStreaming) html += '<span class="cursor">▎</span>';
    contentEl.innerHTML = html;
  }
  scrollToBottom();
}

function addThinking() {
  const div = document.createElement('div');
  div.className = 'thinking-block';
  div.innerHTML =
    '<div class="thinking-header" onclick="toggleThinking(this)">' +
      '<span class="icon">▶</span>' +
      '<span>💭 Thinking...</span>' +
    '</div>' +
    '<div class="thinking-body"></div>';
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function toggleThinking(header) {
  header.querySelector('.icon').classList.toggle('expanded');
  header.nextElementSibling.classList.toggle('show');
}

function addSystem(text) {
  const div = document.createElement('div');
  div.className = 'msg msg-system';
  div.textContent = text;
  messagesEl.appendChild(div);
  scrollToBottom();
}

function addError(text) {
  const div = document.createElement('div');
  div.className = 'msg msg-error';
  div.textContent = '✖ ' + text;
  messagesEl.appendChild(div);
  scrollToBottom();
}

// 工具调用
const toolElements = {};

function addToolUse(data) {
  const div = document.createElement('div');
  div.className = 'tool-block';

  const argsStr = data.args ? JSON.stringify(data.args, null, 2) : '';
  let argsPreview = '';
  if (data.args) {
    if (data.args.command) argsPreview = data.args.command;
    else if (data.args.file_path) argsPreview = data.args.file_path;
    else if (data.args.pattern) argsPreview = data.args.pattern;
    else if (data.args.path) argsPreview = data.args.path;
  }

  div.innerHTML =
    '<div class="tool-header" onclick="toggleTool(this)">' +
      '<span class="icon">▶</span>' +
      '<span class="name">' + escapeHtml(data.toolName) + '</span>' +
      (argsPreview ? '<span style="color:var(--text-dim);font-size:12px;margin-left:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:500px;">' + escapeHtml(argsPreview) + '</span>' : '') +
      '<span class="status loading">⏳ running...</span>' +
    '</div>' +
    '<div class="tool-body">' +
      (argsStr ? '<div style="color:var(--blue);margin-bottom:8px;">Args:\n' + escapeHtml(argsStr) + '</div>' : '') +
      '<div class="tool-output"></div>' +
    '</div>';

  messagesEl.appendChild(div);
  toolElements[data.toolName + '_' + (data.toolId || '')] = div;
  scrollToBottom();
}

function updateToolResult(data) {
  const key = data.toolName + '_' + (data.toolId || '');
  const div = toolElements[key];
  if (div) {
    const statusEl = div.querySelector('.status');
    if (data.isError) {
      statusEl.className = 'status err';
      statusEl.textContent = '✗ ' + data.elapsed.toFixed(1) + 's';
    } else {
      statusEl.className = 'status ok';
      statusEl.textContent = '✓ ' + data.elapsed.toFixed(1) + 's';
    }
    const outputEl = div.querySelector('.tool-output');
    if (data.output) {
      const truncated = data.output.length > 5000 ? data.output.substring(0, 5000) + '\n... (truncated)' : data.output;
      outputEl.textContent = truncated;
    }
    delete toolElements[key];
  } else {
    // 如果没有匹配到，直接创建一个完成的 tool block
    const block = document.createElement('div');
    block.className = 'tool-block';
    const status = data.isError ? '✗' : '✓';
    const statusClass = data.isError ? 'err' : 'ok';
    block.innerHTML =
      '<div class="tool-header" onclick="toggleTool(this)">' +
        '<span class="icon">▶</span>' +
        '<span class="name">' + escapeHtml(data.toolName) + '</span>' +
        '<span class="status ' + statusClass + '">' + status + ' ' + data.elapsed.toFixed(1) + 's</span>' +
      '</div>' +
      '<div class="tool-body">' + escapeHtml(data.output || '') + '</div>';
    messagesEl.appendChild(block);
  }
  scrollToBottom();
}

function toggleTool(header) {
  const icon = header.querySelector('.icon');
  const body = header.nextElementSibling;
  icon.classList.toggle('expanded');
  body.classList.toggle('show');
}

// 权限弹窗
function addPermissionDialog(data) {
  const div = document.createElement('div');
  div.className = 'perm-dialog';
  div.id = 'perm-' + data.id;
  div.innerHTML =
    '<div class="title">🔒 Permission Required: ' + escapeHtml(data.toolName) + '</div>' +
    '<div class="desc">' + escapeHtml(data.description) + '</div>' +
    '<div class="actions">' +
      '<button class="btn-allow" onclick="respondPerm(\'' + data.id + '\', \'allow\')">Allow</button>' +
      '<button class="btn-always" onclick="respondPerm(\'' + data.id + '\', \'allowAlways\')">Allow Always</button>' +
      '<button class="btn-deny" onclick="respondPerm(\'' + data.id + '\', \'deny\')">Deny</button>' +
    '</div>';
  messagesEl.appendChild(div);
  scrollToBottom();
}

function respondPerm(id, response) {
  ws.send(JSON.stringify({ type: 'permission_response', data: { id, response } }));
  const el = document.getElementById('perm-' + id);
  if (el) {
    el.innerHTML = '<div style="color:var(--text-dim)">🔒 Permission: ' + response + '</div>';
  }
}

// Ask User 弹窗
function addAskUserDialog(data) {
  const div = document.createElement('div');
  div.className = 'perm-dialog';
  div.id = 'ask-' + data.id;

  let html = '<div class="title">❓ Question</div>';
  const questions = data.questions || [];
  questions.forEach((q, qi) => {
    html += '<div style="margin-bottom:12px;">';
    html += '<div style="margin-bottom:6px;color:var(--text-bright);">' + escapeHtml(q.question || q.Text || '') + '</div>';
    const options = q.options || q.Options || [];
    options.forEach((opt, oi) => {
      const label = opt.label || opt.Label || '';
      const desc = opt.description || opt.Description || '';
      html += '<label style="display:block;margin:4px 0;cursor:pointer;">' +
        '<input type="radio" name="ask_' + data.id + '_' + qi + '" value="' + escapeHtml(label) + '"> ' +
        '<span style="color:var(--blue)">' + escapeHtml(label) + '</span>' +
        (desc ? ' <span style="color:var(--text-dim);font-size:12px;">— ' + escapeHtml(desc) + '</span>' : '') +
        '</label>';
    });
    // Other 选项
    html += '<label style="display:block;margin:4px 0;cursor:pointer;">' +
      '<input type="radio" name="ask_' + data.id + '_' + qi + '" value="__other__"> ' +
      '<span style="color:var(--text-dim)">Other: </span>' +
      '<input type="text" id="ask_other_' + data.id + '_' + qi + '" style="background:var(--bg-input);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:4px 8px;font-family:inherit;font-size:13px;width:300px;">' +
      '</label>';
    html += '</div>';
  });
  html += '<div class="actions"><button class="btn-allow" onclick="respondAsk(\'' + data.id + '\',' + questions.length + ')">Submit</button></div>';

  div.innerHTML = html;
  messagesEl.appendChild(div);
  scrollToBottom();
}

function respondAsk(id, qCount) {
  const answers = {};
  for (let i = 0; i < qCount; i++) {
    const radios = document.querySelectorAll('input[name="ask_' + id + '_' + i + '"]');
    let val = '';
    radios.forEach(r => {
      if (r.checked) {
        if (r.value === '__other__') {
          val = document.getElementById('ask_other_' + id + '_' + i).value;
        } else {
          val = r.value;
        }
      }
    });
    answers['question_' + i] = val;
  }
  ws.send(JSON.stringify({ type: 'ask_user_response', data: { id, answers } }));
  const el = document.getElementById('ask-' + id);
  if (el) {
    el.innerHTML = '<div style="color:var(--text-dim)">✓ Answered</div>';
  }
}

// 滚动控制
messagesEl.addEventListener('scroll', () => {
  const { scrollTop, scrollHeight, clientHeight } = messagesEl;
  autoScroll = scrollHeight - scrollTop - clientHeight < 60;
});

function scrollToBottom() {
  if (autoScroll) {
    requestAnimationFrame(() => {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    });
  }
}

// 输入处理
function sendMessage() {
  if (slashMenu.classList.contains('show')) { hideSlashMenu(); }
  const text = inputEl.value.trim();
  if (!text || streaming) return;
  addUser(text);
  ws.send(JSON.stringify({ type: 'user_message', data: { content: text } }));
  inputEl.value = '';
  inputEl.style.height = 'auto';
  streaming = true;
  updateUI();
}

function updateUI() {
  sendBtn.disabled = streaming;
  inputEl.disabled = streaming;
  if (!streaming) inputEl.focus();
}

// ── 斜杠命令菜单 ──

function showSlashMenu(filter) {
  const prefix = filter.toLowerCase();
  slashFiltered = allCommands.filter(c => c.name.startsWith(prefix));
  if (slashFiltered.length === 0) { hideSlashMenu(); return; }
  slashCursor = 0;
  renderSlashMenu();
  slashMenu.classList.add('show');
}

function hideSlashMenu() {
  slashMenu.classList.remove('show');
  slashFiltered = [];
  slashCursor = 0;
}

function renderSlashMenu() {
  slashMenu.innerHTML = slashFiltered.map((c, i) =>
    '<div class="slash-item' + (i === slashCursor ? ' active' : '') + '" data-idx="' + i + '">' +
      '<span class="cmd-name">/' + escapeHtml(c.name) + '</span>' +
      '<span class="cmd-desc">' + escapeHtml(c.description) + '</span>' +
    '</div>'
  ).join('');
  // 点击选中
  slashMenu.querySelectorAll('.slash-item').forEach(el => {
    el.addEventListener('mousedown', (e) => {
      e.preventDefault();
      const idx = parseInt(el.dataset.idx);
      selectSlashItem(idx);
    });
  });
  // 滚动到当前高亮项
  const activeEl = slashMenu.querySelector('.active');
  if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });
}

function selectSlashItem(idx) {
  const cmd = slashFiltered[idx];
  if (!cmd) return;
  inputEl.value = '/' + cmd.name + ' ';
  hideSlashMenu();
  inputEl.focus();
}

inputEl.addEventListener('keydown', (e) => {
  if (slashMenu.classList.contains('show')) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      slashCursor = Math.min(slashCursor + 1, slashFiltered.length - 1);
      renderSlashMenu();
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      slashCursor = Math.max(slashCursor - 1, 0);
      renderSlashMenu();
      return;
    }
    if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
      e.preventDefault();
      selectSlashItem(slashCursor);
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      hideSlashMenu();
      return;
    }
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';

  const val = inputEl.value;
  // 检测是否在输入斜杠命令
  if (val.startsWith('/') && !val.includes('\n')) {
    const prefix = val.substring(1).split(' ')[0];
    if (!val.includes(' ')) {
      showSlashMenu(prefix);
    } else {
      hideSlashMenu();
    }
  } else {
    hideSlashMenu();
  }
});

sendBtn.addEventListener('click', sendMessage);

// 启动
connect();
inputEl.focus();
// Product requests are unicast and correlated; they never enter model context.
const pendingProduct = new Map();
let productSequence = 0;
let productView = 'chat';
let creatingMemory = false;
function productRequest(action, fields = {}) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) { reject(new Error('连接尚未就绪')); return; }
    const request_id = String(++productSequence);
    const timer = setTimeout(() => { pendingProduct.delete(request_id); reject(new Error('操作超时，请重试')); }, 15000);
    pendingProduct.set(request_id, { resolve, reject, timer });
    ws.send(JSON.stringify({type:'product_request',data:{action,request_id,...fields}}));
  });
}
function productReply(data) {
  const request = pendingProduct.get(data.request_id);
  if (!request) return;
  clearTimeout(request.timer); pendingProduct.delete(data.request_id);
  if (data.ok) request.resolve(data.result); else request.reject(new Error(data.error));
}
function notice(text) { document.getElementById('product-notice').textContent = text; }
function cardButton(card, title, action) {
  const button = document.createElement('button'); button.type = 'button'; button.textContent = title;
  button.onclick = async () => { button.disabled = true; try { await action(); } catch(error) { notice(error.message); } finally { button.disabled = false; } };
  card.appendChild(button);
}
function showProductView(view) {
  productView = view;
  document.getElementById('workspace-panel').hidden = view === 'chat';
  messagesEl.hidden = view !== 'chat'; document.getElementById('input-area').hidden = view !== 'chat';
  document.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('active',button.dataset.view === view));
  document.getElementById('new-memory').hidden = view !== 'memories';
  document.getElementById('panel-query').disabled = view === 'runs';
  document.getElementById('panel-hint').textContent = view === 'runs' ? '任务和工具步骤会保留。恢复会话后，请明确下一步，工具不会自动重放。' : view === 'memories' ? '自动记忆以 Markdown 为准；此处支持搜索、编辑和删除。用户级记忆适用于所有项目。' : '只搜索当前项目的会话。恢复、重命名或导出历史对话。';
  if (view !== 'chat') refreshProduct().catch(error => notice(error.message));
}
async function refreshProduct() {
  const view = productView;
  if (view === 'chat') return;
  const query = document.getElementById('panel-query').value;
  const items = await productRequest(view,{query});
  if (productView !== view) return;
  const list = document.getElementById('panel-items'); list.replaceChildren();
  if (!items.length) { const p = document.createElement('p'); p.textContent = '暂无记录'; list.appendChild(p); return; }
  for (const item of items) {
    const card = document.createElement('article'); card.className = 'product-card';
    const title = document.createElement('h3'); const detail = document.createElement('p');
    card.append(title,detail);
    if (view === 'history') {
      title.textContent = item.title || '未命名会话'; detail.textContent = item.snippet;
      cardButton(card,'恢复会话',async () => { await productRequest('session_resume',{id:item.session_id}); showProductView('chat'); notice('会话已恢复'); });
      cardButton(card,'重命名',async () => { const title = prompt('会话名称',item.title); if (title !== null) { await productRequest('session_rename',{id:item.session_id,title}); await refreshProduct(); } });
      cardButton(card,'导出',async () => { const result = await productRequest('session_export',{id:item.session_id}); const url = URL.createObjectURL(new Blob([result.text],{type:'application/x-ndjson;charset=utf-8'})); const a = document.createElement('a'); a.href = url; a.download = result.id+'.jsonl'; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000); });
    } else if (view === 'runs') {
      title.textContent = item.prompt || '后台继续执行'; detail.textContent = item.status+' · '+item.source+' · '+item.started_at;
      cardButton(card,'查看步骤',async () => { const result = await productRequest('run',{id:item.id}); let pre = card.querySelector('pre'); if (!pre) { pre=document.createElement('pre'); card.appendChild(pre); } pre.textContent = (result.error ? result.error+'\n' : '') + 'Tokens: '+result.input_tokens+' / '+result.output_tokens+'\n'+result.steps.map(step => step.tool_name+' · '+step.status+'\n'+step.output).join('\n\n'); });
      if (['running','waiting_permission'].includes(item.status)) cardButton(card,'取消',async () => { await productRequest('run_cancel',{id:item.id}); await refreshProduct(); });
      cardButton(card,'恢复会话',async () => { await productRequest('session_resume',{id:item.session_id}); showProductView('chat'); notice('已恢复会话，请说明下一步'); });
    } else {
      title.textContent = item.scope+'/'+item.name; detail.textContent = item.description+'\n'+item.body.slice(0,400);
      cardButton(card,'编辑',async () => openMemoryEditor(item));
      cardButton(card,'删除',async () => { if (confirm('删除记忆 '+item.scope+'/'+item.name+'？')) { await productRequest('memory_forget',{scope:item.scope,name:item.name}); await refreshProduct(); } });
    }
    list.appendChild(card);
  }
}
function openMemoryEditor(item = null) {
  creatingMemory = !item;
  const scope=document.getElementById('memory-scope'), name=document.getElementById('memory-name');
  scope.value=item ? item.scope : 'project'; name.value=item ? item.name : '';
  scope.disabled=!!item; name.disabled=!!item;
  document.getElementById('memory-body').value=item ? item.body : '';
  document.getElementById('memory-editor').showModal();
}
document.querySelectorAll('[data-view]').forEach(button => { button.onclick=() => showProductView(button.dataset.view); });
document.getElementById('panel-search').onsubmit=event => { event.preventDefault(); refreshProduct().catch(error => notice(error.message)); };
document.getElementById('new-memory').onclick=() => openMemoryEditor();
document.getElementById('memory-close').onclick=() => document.getElementById('memory-editor').close();
document.getElementById('memory-form').onsubmit=async event => {
  event.preventDefault();
  const fields={scope:document.getElementById('memory-scope').value,name:document.getElementById('memory-name').value,body:document.getElementById('memory-body').value,create:creatingMemory};
  try { await productRequest('memory_save',fields); document.getElementById('memory-editor').close(); notice('记忆已保存'); await refreshProduct(); } catch(error) { notice(error.message); }
};
document.getElementById('backup-db').onclick=async () => { try { const result=await productRequest('backup'); notice('备份已保存：'+result.path); } catch(error) { notice(error.message); } };
</script>
</body>
</html>"""


from nonocode.web_workbench import WORKBENCH_HTML, WORKBENCH_JS
INDEX_HTML = INDEX_HTML.replace('<dialog id="memory-editor">', WORKBENCH_HTML + '<dialog id="memory-editor">')
INDEX_HTML = INDEX_HTML.replace('<p>保存后会用于后续对话。', '<label>原话依据或编辑说明 <textarea id="memory-evidence" maxlength="2000"></textarea></label><label>适用主题 <input id="memory-topic" maxlength="100"></label><label>过期时间（ISO，留空长期有效）<input id="memory-expires"></label><label><input type="checkbox" id="memory-enabled" checked>参与后续检索</label><p>保存后会用于后续对话。')
INDEX_HTML = INDEX_HTML.replace('</script>\n</body>', WORKBENCH_JS + '\n</script>\n</body>')
INDEX_HTML = INDEX_HTML.replace('}, 15000);', "}, ['settings_test','settings_save','backup','artifact_download'].includes(action) ? 45000 : 15000);")
INDEX_HTML = INDEX_HTML.replace('dialog input,dialog select,dialog textarea,#panel-query', 'dialog input,dialog select,dialog textarea,#panel-query,.product-card input,.product-card select')
INDEX_HTML = INDEX_HTML.replace('</style>', 'dialog pre {white-space:pre-wrap;max-height:400px;overflow:auto;} .product-card label {display:block;margin:12px 0;} .product-card label input:not([type=checkbox]),.product-card label select {display:block;width:min(100%,560px);} #workspace-nav {flex-wrap:wrap;}\n</style>')
