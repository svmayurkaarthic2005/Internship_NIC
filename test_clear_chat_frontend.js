/**
 * Behavioural tests for the New Chat / "clear" lifecycle in frontend/js/chat.js.
 *
 * Loads the real chat.js + chatStorage.js into a Node vm with a minimal fake
 * DOM and a fetch whose responses the test releases by hand, so the races are
 * reproduced deterministically: a stream still running when New Chat is
 * clicked, a session request that fails / times out / answers late.
 *
 *     node test_clear_chat_frontend.js
 *
 * Run from test_clear_chat.py as well. No browser, no backend.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let failures = 0, passes = 0;
function check(name, cond, detail) {
    if (cond) { passes++; console.log(`  PASS  ${name}`); }
    else { failures++; console.log(`  FAIL  ${name}${detail ? '  -- ' + detail : ''}`); }
}

// ---------------------------------------------------------------- fake DOM
class El {
    constructor(tag) {
        this.tagName = tag; this.children = []; this.parentNode = null; this.id = '';
        this.className = ''; this.style = {}; this.value = ''; this.disabled = false;
        this.attrs = {}; this.handlers = {}; this._html = ''; this._q = {};
        this.scrollHeight = 0; this.scrollTop = 0;
        const cls = new Set();
        this.classList = {
            add: c => cls.add(c), remove: c => cls.delete(c), contains: c => cls.has(c),
            toggle: (c, on) => { if (on === undefined ? !cls.has(c) : on) cls.add(c); else cls.delete(c); },
        };
    }
    set innerHTML(v) { this._html = v; if (v === '') { this.children.forEach(c => c.parentNode = null); this.children = []; } this._q = {}; }
    get innerHTML() { return this._html; }
    set textContent(v) { this.innerHTML = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
    get textContent() { return this._html; }
    appendChild(c) { if (c.parentNode) c.remove(); c.parentNode = this; this.children.push(c); return c; }
    insertBefore(c) { return this.appendChild(c); }
    remove() { if (this.parentNode) { const p = this.parentNode; p.children = p.children.filter(x => x !== this); this.parentNode = null; } }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; if (k === 'id') this.id = ''; }
    addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); }
    click() { (this.handlers.click || []).forEach(f => f({ preventDefault() {} })); }
    querySelector(sel) {
        // "the element this markup would contain": one stub per selector, and
        // the notice's retry button is only there when the markup carries it
        if (sel === '.notice-new-chat-btn' && !this._html.includes('notice-new-chat-btn')) return null;
        if (!this._q[sel]) { this._q[sel] = new El('div'); this._q[sel].parentNode = this; if (sel.startsWith('#')) this._q[sel].id = sel.slice(1); }
        return this._q[sel];
    }
    querySelectorAll() { return []; }
    focus() {} blur() {} scrollIntoView() {}
    get text() { return [this._html, ...this.children.map(c => c.text), ...Object.values(this._q).map(c => c.text)].join(' '); }
}

function makeContext() {
    const ids = {};
    for (const id of ['chatMessages', 'messageInput', 'sendBtn', 'quickSuggestions', 'languageIndicator',
                      'languageText', 'newChatBtn', 'logoutBtn', 'mobileMenuBtn', 'chatSidebar',
                      'sessionHistoryList', 'toastContainer', 'officerName', 'officerRole', 'officerAvatar']) {
        ids[id] = new El('div'); ids[id].id = id;
    }
    const chatMessages = ids.chatMessages;
    const find = (node, id) => { for (const c of node.children) { if (c.id === id) return c; const r = find(c, id); if (r) return r; } return null; };
    const body = new El('body');
    const document = {
        body,
        getElementById: id => ids[id] || find(chatMessages, id) || find(body, id),
        createElement: t => new El(t),
        querySelector: () => null, querySelectorAll: () => [],
        addEventListener() {},
    };
    const store = () => { const m = new Map(); return {
        getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
        removeItem: k => m.delete(k), clear: () => m.clear(), _m: m }; };
    const localStorage = store(), sessionStorage = store();
    sessionStorage.setItem('officer_data', JSON.stringify({ employee_id: 'T1', access_token: 'tok' }));

    const toasts = [];
    const timers = new Set();
    const ctx = {
        console: { log() {}, warn() {}, info() {}, debug() {}, error() {} },
        document, localStorage, sessionStorage, toasts,
        window: null, location: { href: '' },
        navigator: { clipboard: { writeText: async () => {} } },
        setTimeout: (f, ms) => { const t = setTimeout(f, Math.min(ms, 50000)); t.unref(); timers.add(t); return t; },
        clearTimeout: t => clearTimeout(t),
        setInterval: (f, ms) => { const t = setInterval(f, ms); t.unref(); return t; },
        clearInterval: t => clearInterval(t),
        AbortController, TextDecoder, TextEncoder, JSON, Promise, Date, Math, Error,
        fetch: null,
    };
    ctx.window = ctx;
    vm.createContext(ctx);
    const root = __dirname;
    vm.runInContext(fs.readFileSync(path.join(root, 'frontend/js/chatStorage.js'), 'utf8'), ctx, { filename: 'chatStorage.js' });
    vm.runInContext(fs.readFileSync(process.env.CHAT_JS || path.join(root, 'frontend/js/chat.js'), 'utf8'), ctx, { filename: 'chat.js' });
    // capture toasts instead of drawing them
    vm.runInContext('showToast = (m, t) => { toasts.push([m, t]); };', ctx);
    vm.runInContext("officerData = { access_token: 'tok', employee_id: 'T1' };", ctx);
    return { ctx, ids, run: code => vm.runInContext(code, ctx) };
}

// ------------------------------------------------------- controllable fetch
function deferred() { let resolve, reject; const p = new Promise((a, b) => { resolve = a; reject = b; }); return { p, resolve, reject }; }
const flush = () => new Promise(r => setImmediate(r));
async function settle(n = 8) { for (let i = 0; i < n; i++) await flush(); }

/** A streaming response whose frames the test pushes; abort rejects the read. */
function streamResponse(signal) {
    const queue = []; let waiter = null; let ended = false; let aborted = false;
    const enc = new TextEncoder();
    const abortErr = () => { const e = new Error('The operation was aborted.'); e.name = 'AbortError'; return e; };
    signal.addEventListener('abort', () => { aborted = true; if (waiter) { waiter.reject(abortErr()); waiter = null; } });
    const reader = {
        read() {
            if (aborted) return Promise.reject(abortErr());
            if (queue.length) return Promise.resolve({ value: queue.shift(), done: false });
            if (ended) return Promise.resolve({ value: undefined, done: true });
            waiter = deferred(); return waiter.p;
        },
    };
    const push = obj => { const v = enc.encode(`data: ${JSON.stringify(obj)}\n\n`); if (waiter) { waiter.resolve({ value: v, done: false }); waiter = null; } else queue.push(v); };
    const end = () => { ended = true; if (waiter) { waiter.resolve({ value: undefined, done: true }); waiter = null; } };
    return { response: { ok: true, status: 200, body: { getReader: () => reader }, text: async () => '' }, push, end };
}

function installFetch(h) {
    const calls = { stream: [], session: [] };
    h.ctx.fetch = (url, opts = {}) => {
        if (url.endsWith('/api/v1/chat/stream')) {
            const s = streamResponse(opts.signal);
            const d = deferred();
            calls.stream.push({ body: JSON.parse(opts.body), ...s, release: () => d.resolve(s.response), signal: opts.signal });
            opts.signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; d.reject(e); });
            return d.p;
        }
        if (url.endsWith('/api/v1/chat/sessions') && opts.method === 'POST') {
            const d = deferred();
            calls.session.push({ d, signal: opts.signal });
            if (opts.signal) opts.signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; d.reject(e); });
            return d.p;
        }
        return Promise.resolve({ ok: false, status: 404, json: async () => ({}), text: async () => '' });
    };
    return calls;
}
const okSession = id => ({ ok: true, status: 200, json: async () => ({ data: { session_id: id } }), text: async () => '' });
const storedHistory = h => JSON.parse(h.ctx.localStorage.getItem('sis_chat_history_T1') || '[]');
const storedSession = h => h.ctx.localStorage.getItem('sis_current_session_id_T1');

async function send(h, text) {
    h.ids.messageInput.value = text;
    const p = h.run('sendMessage()');
    await settle();
    return p;
}

// ------------------------------------------------------------------ cases
async function caseNewChatDuringStream() {
    console.log('\n[1] New Chat while an answer is streaming, then a new question');
    const h = makeContext(); const calls = installFetch(h);
    h.run("currentSessionId = 'S-old'");
    const p1 = send(h, 'show my pending applications');
    calls.stream[0].release(); await settle();
    calls.stream[0].push({ content: 'OLD-ANSWER part 1 ' }); await settle();
    check('the old answer started streaming into the page', h.ids.chatMessages.text.includes('OLD-ANSWER'));
    check('isTyping is true while streaming', h.run('isTyping') === true);

    const nc = h.run('handleNewChat()'); await settle();
    check('the old stream was aborted', calls.stream[0].signal.aborted === true);
    check('the session id is invalidated while the new one is pending', h.run('currentSessionId') === null);
    h.ids.messageInput.value = 'what is ISD?'; h.run('handleInputChange()');
    check('send stays disabled until the new session is confirmed', h.ids.sendBtn.disabled === true);
    calls.session[0].d.resolve(okSession('S-new')); await nc; await p1; await settle();

    check('the new session id is active', h.run('currentSessionId') === 'S-new');
    check('the new session id is stored', storedSession(h) === 'S-new');
    check('isTyping restored', h.run('isTyping') === false);
    check('no active request controller left behind', h.run('activeRequestController') === null);
    check('the stop state is gone from the send button', !h.ids.sendBtn.classList.contains('btn-send-stop'));
    check('the old answer is not on the page', !h.ids.chatMessages.text.includes('OLD-ANSWER'));
    check('no typing indicator left', h.ctx.document.getElementById('typingIndicator') === null);
    check('the cleared history stays empty', storedHistory(h).length === 0);
    check('no error toast from the aborted old request',
          !h.ctx.toasts.some(([m, t]) => t === 'error' || /timed out|stopped/i.test(m)), JSON.stringify(h.ctx.toasts));

    const p2 = send(h, 'what is ISD?');
    check('the new question goes to the new session', calls.stream[1].body.session_id === 'S-new');
    check('the new question carries no old history', calls.stream[1].body.chat_history.length === 0);
    calls.stream[1].release(); await settle();
    calls.stream[1].push({ content: 'ISD means Involving Sub-Division.' });
    calls.stream[1].push({ done: true }); calls.stream[1].end(); await p2; await settle();
    const hist = storedHistory(h);
    check('the new conversation is stored, and only it',
          hist.length === 2 && hist[1].content.startsWith('ISD means') && !JSON.stringify(hist).includes('OLD-ANSWER'),
          JSON.stringify(hist));
    check('send state restored after the new answer', h.run('isTyping') === false && h.run('activeRequestController') === null);
}

async function caseStaleFrameAfterClear() {
    console.log('\n[2] An old stream that still delivers after New Chat cannot write anything');
    const h = makeContext(); const calls = installFetch(h);
    h.run("currentSessionId = 'S-old'");
    // a fetch that ignores abort (the server answered just as New Chat was clicked)
    const realFetch = h.ctx.fetch;
    let late = null;
    h.ctx.fetch = (url, opts) => {
        if (url.endsWith('/chat/stream')) {
            const s = streamResponse(new AbortController().signal); late = s;
            return new Promise(r => { s.release = () => r(s.response); });
        }
        return realFetch(url, opts);
    };
    const p1 = send(h, 'show my pending applications');
    const nc = h.run('handleNewChat()'); await settle();
    calls.session[0].d.resolve(okSession('S-new')); await nc;
    late.release(); late.push({ content: 'STALE-ANSWER' }); late.push({ done: true }); late.end();
    await p1; await settle();
    check('the late answer never reaches the page', !h.ids.chatMessages.text.includes('STALE-ANSWER'));
    check('the late answer never reaches localStorage', !JSON.stringify(storedHistory(h)).includes('STALE-ANSWER'));
    check('the new session id survives the late answer', h.run('currentSessionId') === 'S-new');
    check('the late request did not reset a newer send state', h.run('isTyping') === false);
}

async function caseStopStillWorks() {
    console.log('\n[3] Stop keeps the partial answer (unchanged behaviour)');
    const h = makeContext(); const calls = installFetch(h);
    h.run("currentSessionId = 'S1'");
    const p = send(h, 'explain ISD');
    calls.stream[0].release(); await settle();
    calls.stream[0].push({ content: 'PARTIAL text' }); await settle();
    h.run('stopResponse()'); await p; await settle();
    const hist = storedHistory(h);
    check('the partial answer is kept and saved', hist.some(m => m.role === 'assistant' && m.content.includes('PARTIAL')));
    check('a stopped notice is shown', h.ids.chatMessages.text.includes('Response stopped'));
    check('send state restored after Stop', h.run('isTyping') === false && h.run('activeRequestController') === null);
}

async function caseClearAction() {
    console.log('\n[4] The streamed action="clear_chat" wipes the conversation');
    const h = makeContext(); const calls = installFetch(h);
    h.run("currentSessionId = 'S1'");
    h.ctx.chatStorage.addMessage('user', 'status of 2026/0154/28/001280');
    h.ctx.chatStorage.addMessage('assistant', 'OLD-RECORD answer');
    const p = send(h, 'clear');
    calls.stream[0].release(); await settle();
    calls.stream[0].push({ content: 'Conversation cleared.', action: 'clear_chat' });
    calls.stream[0].push({ done: true }); calls.stream[0].end(); await settle();
    check('a new session is requested', calls.session.length === 1);
    calls.session[0].d.resolve(okSession('S2')); await p; await settle();
    check('the new session is active', h.run('currentSessionId') === 'S2' && storedSession(h) === 'S2');
    check('the stored history is empty (the clear turn is not saved either)', storedHistory(h).length === 0);
    check('the old record is gone from the page', !h.ids.chatMessages.text.includes('OLD-RECORD'));
    check('"Conversation cleared" toast', h.ctx.toasts.some(([m]) => m === 'Conversation cleared'));
    check('send state restored', h.run('isTyping') === false && h.run('activeRequestController') === null);
}

async function caseSessionFailures() {
    const failures = [
        ['HTTP 500', d => d.resolve({ ok: false, status: 500, text: async () => 'boom', json: async () => ({}) })],
        ['network failure', d => d.reject(new TypeError('Failed to fetch'))],
        ['malformed payload', d => d.resolve({ ok: true, status: 200, json: async () => ({ data: {} }), text: async () => '' })],
        ['non-JSON payload', d => d.resolve({ ok: true, status: 200, json: async () => { throw new SyntaxError('bad'); }, text: async () => '' })],
    ];
    for (const [label, fail] of failures) {
        console.log(`\n[5] New Chat when session creation fails: ${label}`);
        const h = makeContext(); const calls = installFetch(h);
        h.run("currentSessionId = 'S-old'");
        h.ctx.chatStorage.saveSessionId('S-old');
        const nc = h.run('handleNewChat()'); await settle();
        fail(calls.session[0].d); await nc; await settle();
        check('the old session id is NOT kept', h.run('currentSessionId') === null);
        check('the old session id is not left in storage', storedSession(h) === null);
        h.ids.messageInput.value = 'what is ISD?'; h.run('handleInputChange()');
        check('send stays disabled', h.ids.sendBtn.disabled === true);
        const before = calls.stream.length;
        await h.run('sendMessage()');
        check('a message cannot be sent to the old session', calls.stream.length === before);
        check('an error message is shown', h.ids.chatMessages.text.includes('Could not start a new chat'));
        check('an error toast is shown', h.ctx.toasts.some(([, t]) => t === 'error'));
        check('the transition is not stuck in flight', h.run('newChatInFlight') === null);
        // retry through the notice's button
        const btn = h.ids.chatMessages.children[h.ids.chatMessages.children.length - 1].querySelector('.notice-new-chat-btn');
        check('a retry button is offered', !!btn);
        if (!btn) continue;
        btn.click(); await settle();
        calls.session[1].d.resolve(okSession('S-retry')); await h.run('newChatInFlight || Promise.resolve()'); await settle();
        check('retry yields a usable session', h.run('currentSessionId') === 'S-retry' && storedSession(h) === 'S-retry');
        h.run('handleInputChange()');
        check('send is enabled again after the retry', h.ids.sendBtn.disabled === false);
    }
}

async function caseSessionTimeout() {
    console.log('\n[6] New Chat when session creation times out');
    const h = makeContext(); const calls = installFetch(h);
    h.run("currentSessionId = 'S-old'");
    // the session request's own 5 s timer, shortened: the real abort path runs
    const realSetTimeout = h.ctx.setTimeout;
    h.ctx.setTimeout = (f, ms) => (ms === 5000 ? setTimeout(f, 5) : realSetTimeout(f, ms));
    const nc = h.run('handleNewChat()');
    await nc; await settle();
    check('the session request was aborted by its timer', calls.session[0].signal.aborted === true);
    check('timeout leaves no session', h.run('currentSessionId') === null);
    check('timeout message is shown', h.ids.chatMessages.text.includes('timed out'), h.ids.chatMessages.text.slice(0, 300));
    check('UI usable (not typing, no request)', h.run('isTyping') === false && h.run('activeRequestController') === null);
}

async function caseDoubleClick() {
    console.log('\n[7] Two rapid New Chat clicks (and a "clear" meanwhile) open ONE session');
    const h = makeContext(); const calls = installFetch(h);
    h.run("currentSessionId = 'S-old'");
    const a = h.run('handleNewChat()'); const b = h.run('handleNewChat()'); const c = h.run("handleNewChat({toast: 'Conversation cleared'})");
    await settle();
    check('only one session request is made', calls.session.length === 1, `got ${calls.session.length}`);
    check('all callers share one transition', a === b && b === c);
    calls.session[0].d.resolve(okSession('S-one')); await a; await settle();
    check('that session is active', h.run('currentSessionId') === 'S-one');
}

async function caseLateSessionReply() {
    console.log('\n[8] A late reply from an older session request cannot replace the newer session');
    const h = makeContext(); const calls = installFetch(h);
    const first = h.run('createNewSession()');
    const second = h.run('createNewSession()');
    await settle();
    calls.session[1].d.resolve(okSession('S-newer')); await second;
    calls.session[0].d.resolve(okSession('S-older')); const r = await first;
    check('the older attempt reports itself superseded', r === null);
    check('the newer session id stays', h.run('currentSessionId') === 'S-newer');
}

(async () => {
    await caseNewChatDuringStream();
    await caseStaleFrameAfterClear();
    await caseStopStillWorks();
    await caseClearAction();
    await caseSessionFailures();
    await caseSessionTimeout();
    await caseDoubleClick();
    await caseLateSessionReply();
    console.log(`\n${passes} passed, ${failures} failed`);
    process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
