/**
 * Chat Module - Phase 6
 * Handles chatbot interactions, message rendering, and session management
 */

// Configuration
const API_BASE_URL = window.API_BASE_URL || 'http://localhost:8000';
if (!window.API_BASE_URL) {
    window.API_BASE_URL = API_BASE_URL;
}

// State Management
let currentSessionId = null;
let isTyping = false;
let messageHistory = [];
let officerData = null;
let activeRequestController = null; // AbortController for the in-flight send, so the stop button can cancel it
let userStoppedResponse = false;    // distinguishes a manual stop from a timeout/network abort
let longConversationNoticeShown = false; // "start a new chat" notice is shown once per session
// Bumped by every New Chat / typed "clear". A request started under an older
// value belongs to a conversation that is gone: it may not touch the DOM,
// localStorage or the send state of the current one.
let conversationGen = 0;
let sessionGen = 0;          // latest session-creation attempt; an older attempt's reply is dropped
let newChatInFlight = null;  // the New Chat transition in progress, so a double click joins it
const CONTEXT_MESSAGE_LIMIT = 40;         // messages sent to the backend as context (older ones are dropped)

// Shell-style input history (Up/Down arrows recall previously sent
// messages, like a terminal's command history). -1 means "not browsing" --
// the officer's own in-progress text stands. Browsing starts fresh from the
// newest message every time; sending one resets it.
let inputHistoryIndex = -1;
let inputHistoryDraft = '';

// DOM Elements
const chatMessages = document.getElementById('chatMessages');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const quickSuggestions = document.getElementById('quickSuggestions');
const languageIndicator = document.getElementById('languageIndicator');
const languageText = document.getElementById('languageText');
const newChatBtn = document.getElementById('newChatBtn');
const logoutBtn = document.getElementById('logoutBtn');
const mobileMenuBtn = document.getElementById('mobileMenuBtn');
const chatSidebar = document.getElementById('chatSidebar');
const sessionHistoryList = document.getElementById('sessionHistoryList');

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    initChat();
});

/**
 * Initialize chat application
 */
async function initChat() {
    console.log('=== Chat Initialization Started ===');
    
    try {
        // Check authentication
        console.log('Step 1: Checking authentication...');
        const officer = await checkAuth();
        if (!officer) {
            console.error('Authentication failed - no officer data');
            return;
        }
        console.log('✓ Auth successful:', officer.employee_id);
        
        officerData = officer;
        
        // Update officer info
        console.log('Step 2: Updating officer info...');
        updateOfficerInfo(officer);
        console.log('✓ Officer info updated');
        
        // Create or load session
        console.log('Step 3: Managing chat session...');
        await loadOrCreateSession();
        console.log('✓ Session ready:', currentSessionId);
        
        // Load chat history from localStorage
        console.log('Step 4: Loading chat history from localStorage...');
        if (window.chatStorage) {
            messageHistory = window.chatStorage.load();
            console.log(`✓ Loaded ${messageHistory.length} messages from storage`);

            // The browser copy can be gone (cleared site data, another tab's
            // officer id, a different browser) while the conversation is still
            // on the server -- restore it from there rather than showing an
            // empty chat.
            if (messageHistory.length === 0 && currentSessionId) {
                messageHistory = await restoreHistoryFromServer(currentSessionId);
            }
            
            // Render previous messages if they exist
            if (messageHistory.length > 0) {
                renderMessagesFromHistory();
            } else {
                renderWelcomeMessage();
            }
        } else {
            console.warn('chatStorage not loaded, rendering welcome');
            renderWelcomeMessage();
        }
        
        // Setup event listeners
        console.log('Step 5: Setting up event listeners...');
        setupEventListeners();
        console.log('✓ Event listeners setup');

        initAttachmentsPanel();
        initConnectionStatus();

        // Initialize Lucide icons
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
            console.log('✓ Lucide icons initialized');
        }
        
        console.log('=== Chat Initialization Complete ===');
        
    } catch (error) {
        console.error('=== INITIALIZATION ERROR ===');
        console.error('Error details:', error);
        console.error('Stack trace:', error.stack);
        
        // Still show the chat interface but with error message
        if (chatMessages) {
            chatMessages.innerHTML = `
                <div class="message message-assistant">
                    <div class="message-avatar">
                        <i data-lucide="alert-circle" class="avatar-icon"></i>
                    </div>
                    <div class="message-content-wrapper">
                        <div class="message-content">
                            <strong>Connection Error</strong><br><br>
                            Unable to connect to the chat server.<br><br>
                            Error: ${error.message}<br><br>
                            Please try:<br>
                            • Refreshing the page<br>
                            • Logging out and logging back in<br>
                            • Contacting support if the issue persists
                        </div>
                    </div>
                </div>
            `;
        }
        
        // Still setup basic listeners
        setupEventListeners();
        
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
    }
}

/**
 * Check authentication and get officer data
 * NOTE: auth.js also defines checkAuth() but returns only result.data (missing access_token).
 * This version merges sessionStorage data (which has access_token) with server data.
 */
async function checkAuth() {
    const storedData = sessionStorage.getItem('officer_data');
    
    if (!storedData) {
        window.location.href = 'login.html';
        return null;
    }
    
    try {
        const data = JSON.parse(storedData);
        
        // Verify session with a timeout so a dead backend doesn't hang forever
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 8000);
        
        const response = await fetch(`${API_BASE_URL}/api/v1/auth/me`, {
            headers: {
                'Authorization': `Bearer ${data.access_token}`
            },
            credentials: 'include',
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            sessionStorage.removeItem('officer_data');
            window.location.href = 'login.html';
            return null;
        }
        
        const result = await response.json();
        // Merge: keep access_token from sessionStorage, enrich with server profile data
        return { ...data, ...result.data, access_token: data.access_token };
        
    } catch (error) {
        console.error('Auth check failed:', error);
        sessionStorage.removeItem('officer_data');
        window.location.href = 'login.html';
        return null;
    }
}

/**
 * Update officer information in UI
 */
function updateOfficerInfo(officer) {
    // Header - check if elements exist before updating
    const officerNameEl = document.getElementById('officerName');
    const employeeIdEl = document.getElementById('employeeId');
    const officerCardNameEl = document.getElementById('officerCardName');
    const officerJurisdictionEl = document.getElementById('officerJurisdiction');
    const officerInitialsEl = document.getElementById('officerInitials');
    const officerAvatarEl = document.getElementById('officerAvatar');
    
    if (officerNameEl) officerNameEl.textContent = officer.officer_name;
    if (employeeIdEl) employeeIdEl.textContent = officer.employee_id;
    if (officerCardNameEl) officerCardNameEl.textContent = officer.officer_name;
    if (officerJurisdictionEl) {
        officerJurisdictionEl.textContent = `${officer.jurisdiction_type}: ${officer.jurisdiction_name}`;
    }
    
    // Avatar initials
    const initials = officer.officer_name
        .split(' ')
        .map(n => n[0])
        .join('')
        .toUpperCase()
        .substring(0, 2);
    
    if (officerInitialsEl) officerInitialsEl.textContent = initials;
    if (officerAvatarEl) officerAvatarEl.textContent = initials;
    
    console.log('Officer info updated:', officer.employee_id);
}

/**
 * Create new chat session
 */
async function createNewSession() {
    const myGen = ++sessionGen;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000); // 5 second timeout
    
    try {
        const sessionHeaders = { 'Content-Type': 'application/json' };
        if (officerData && officerData.access_token) {
            sessionHeaders['Authorization'] = `Bearer ${officerData.access_token}`;
        }
        const response = await fetch(`${API_BASE_URL}/api/v1/chat/sessions`, {
            method: 'POST',
            headers: sessionHeaders,
            credentials: 'include',
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`HTTP ${response.status}: ${errorText}`);
        }
        
        let data = null;
        try { data = await response.json(); } catch (_) { /* reported below */ }
        const sessionId = data && data.data && data.data.session_id;
        if (typeof sessionId !== 'string' || !sessionId) {
            throw new Error('Invalid session response from server');
        }
        // A newer attempt started while this one was in flight: its session wins.
        if (myGen !== sessionGen) return null;
        currentSessionId = sessionId;
        console.log('Session created:', currentSessionId);
        return sessionId;

    } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === 'AbortError') {
            throw new Error('Session creation timed out');
        }
        throw error;
    }
}

/**
 * Load existing session from storage or create new one
 */
async function loadOrCreateSession() {
    // Try to load session ID from localStorage
    if (window.chatStorage) {
        const savedSessionId = window.chatStorage.loadSessionId();
        if (savedSessionId) {
            currentSessionId = savedSessionId;
            console.log('✓ Loaded existing session:', currentSessionId);
            return;
        }
    }
    
    // No saved session, create new one
    await createNewSession();
    
    // Save new session ID
    if (window.chatStorage && currentSessionId) {
        window.chatStorage.saveSessionId(currentSessionId);
    }
}

/** Fetch this session's saved messages and put them back in local storage. */
async function restoreHistoryFromServer(sessionId) {
    try {
        const r = await fetch(`${API_BASE_URL}/api/v1/chat/sessions/${sessionId}/history?limit=100`,
                              { credentials: 'include',
                                headers: officerData && officerData.access_token
                                    ? { 'Authorization': `Bearer ${officerData.access_token}` } : {} });
        if (!r.ok) return [];
        const msgs = ((await r.json()).data || {}).messages || [];
        if (msgs.length) window.chatStorage.save(msgs);
        return msgs;
    } catch (e) {
        console.log('Could not restore history from server:', e.message);
        return [];
    }
}

/**
 * Render messages from history stored in sessionStorage
 */
function renderMessagesFromHistory() {
    if (!messageHistory || messageHistory.length === 0) {
        renderWelcomeMessage();
        return;
    }
    
    // Clear chat
    chatMessages.innerHTML = '';
    
    // Render each message
    messageHistory.forEach(msg => {
        renderMessage(msg.role, msg.content, msg.timestamp, msg.language || 'auto', true, msg.tableData || null);
    });
    
    // Scroll to bottom
    scrollToBottom();
}

/**
 * Render welcome message
 */
function renderWelcomeMessage() {
    // HTML, so it is shown as a greeting with a list -- not turned into a FIELD | DETAILS record card
    const welcomeMessage = `<p>வணக்கம்! I'm your SIS AI Assistant. I can help you with:</p>
<ul>
<li>Survey numbers and sub-divisions</li>
<li>Applications of every service code (ISD, NISD, F-Line, modification, TSLR and the rest)</li>
<li>Field visit scheduling</li>
<li>Application status and record details (owners, transfers, recommendations)</li>
<li>Workflow questions</li>
<li>Workload management</li>
</ul>
<p>How can I assist you today?</p>`;
    
    renderMessage('assistant', welcomeMessage, new Date().toISOString(), 'en', false);
}

/**
 * Setup event listeners
 */
function setupEventListeners() {
    // Send button — doubles as a stop button while a response is streaming
    if (sendBtn) {
        sendBtn.addEventListener('click', () => {
            if (isTyping) {
                stopResponse();
            } else {
                sendMessage();
            }
        });
    }
    
    // Message input
    if (messageInput) {
        messageInput.addEventListener('input', handleInputChange);
        messageInput.addEventListener('keydown', handleKeyDown);
    }

    // Esc stops a streaming response from anywhere on the page -- the officer
    // should not have to find the button to call it off.
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isTyping) {
            e.preventDefault();
            stopResponse();
        }
    });
    
    // Quick suggestions
    if (quickSuggestions) {
        const chips = quickSuggestions.querySelectorAll('.suggestion-chip');
        chips.forEach(chip => {
            chip.addEventListener('click', () => {
                const message = chip.getAttribute('data-message');
                messageInput.value = message;
                handleInputChange();
                sendMessage();
            });
        });
    }
    
    // New chat button
    if (newChatBtn) {
        newChatBtn.addEventListener('click', handleNewChat);
    }
    
    // Logout button
    if (logoutBtn) {
        logoutBtn.addEventListener('click', handleLogout);
    }
    
    // Mobile menu button
    if (mobileMenuBtn) {
        mobileMenuBtn.addEventListener('click', toggleMobileSidebar);
    }
    
    // Attachment button
    const attachBtn = document.querySelector('.btn-icon-action-new[aria-label="Attach file"]');
    if (attachBtn) {
        attachBtn.addEventListener('click', handleFileAttachment);
    }
}

/**
 * Handle input change (enable/disable send button, detect language)
 */
function handleInputChange() {
    const text = messageInput.value.trim();
    // While a response is streaming, the button stays enabled but switches to
    // "stop" mode (see setSendButtonState) instead of being disabled.
    if (!isTyping) {
        sendBtn.disabled = !text || !currentSessionId;
    }
    
    // Detect language
    if (text) {
        const language = detectLanguage(text);
        updateLanguageIndicator(language);
        
        // Auto-resize textarea
        messageInput.style.height = 'auto';
        messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
    }
}

/**
 * Toggle the send button between "send" and "stop" appearance/behaviour.
 */
function setSendButtonState(streaming) {
    if (!sendBtn) return;
    // Which icon shows is entirely up to the .btn-send-stop class (see
    // chatbot.css). Setting display on the icons from here does not survive
    // the icon library re-rendering the send arrow, which left the stop
    // square drawn on top of it.
    sendBtn.classList.toggle('btn-send-stop', streaming);
    const label = streaming ? 'Stop generating' : 'Send message';
    sendBtn.setAttribute('aria-label', label);
    sendBtn.setAttribute('title', streaming ? 'Stop generating (Esc)' : 'Send message');

    if (streaming) {
        sendBtn.disabled = false;
    } else {
        // Restore normal enabled/disabled logic based on current input text;
        // nothing can be sent until a session is confirmed by the server
        sendBtn.disabled = !messageInput.value.trim() || !currentSessionId;
    }
}

/**
 * Stop the in-flight response (user clicked the send button while it was
 * showing the stop icon). Aborts the fetch/stream; whatever text has already
 * streamed in is kept and saved, matching how ChatGPT-style stop buttons behave.
 */
function stopResponse() {
    if (!activeRequestController) return;
    userStoppedResponse = true;
    activeRequestController.abort();
}

// Safe wrapper — languageIndicator/languageText don't exist in the current chatbot.html
const _languageText = document.getElementById('languageText');
const _languageIndicator = document.getElementById('languageIndicator');

/**
 * The officer's own previously SENT messages, oldest first -- what Up/Down
 * cycles through. Pulled from the live in-memory mirror rather than
 * re-reading localStorage on every keystroke.
 */
function getSentMessages() {
    return (messageHistory || [])
        .filter(m => m && m.role === 'user' && m.content)
        .map(m => m.content);
}

/** True when the caret has nothing but the first/last line above/below it --
 * the same rule terminals and chat apps use so Up/Down still move the caret
 * normally inside a multi-line draft instead of always recalling history. */
function isCaretOnFirstLine(el) {
    return el.value.slice(0, el.selectionStart).indexOf('\n') === -1;
}
function isCaretOnLastLine(el) {
    return el.value.slice(el.selectionEnd).indexOf('\n') === -1;
}

function setMessageInputValue(text) {
    messageInput.value = text;
    messageInput.selectionStart = messageInput.selectionEnd = text.length;
    handleInputChange(); // reuses the existing resize/button/language logic
}

/**
 * Handle keyboard shortcuts
 */
function handleKeyDown(event) {
    // Enter without Shift sends message. Ignored while a response is
    // streaming — the button is enabled during that time to act as Stop,
    // not Send.
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        if (!sendBtn.disabled && !isTyping) {
            sendMessage();
        }
        return;
    }

    if (event.key === 'ArrowUp' && isCaretOnFirstLine(messageInput)) {
        const sent = getSentMessages();
        if (!sent.length) return;
        if (inputHistoryIndex === -1) {
            inputHistoryDraft = messageInput.value;
            inputHistoryIndex = sent.length - 1;
        } else if (inputHistoryIndex > 0) {
            inputHistoryIndex -= 1;
        } else {
            return; // already at the oldest message
        }
        event.preventDefault();
        setMessageInputValue(sent[inputHistoryIndex]);
        return;
    }

    if (event.key === 'ArrowDown' && isCaretOnLastLine(messageInput)) {
        if (inputHistoryIndex === -1) return; // not browsing history
        event.preventDefault();
        const sent = getSentMessages();
        if (inputHistoryIndex < sent.length - 1) {
            inputHistoryIndex += 1;
            setMessageInputValue(sent[inputHistoryIndex]);
        } else {
            // Past the newest message — back to whatever was being typed.
            inputHistoryIndex = -1;
            setMessageInputValue(inputHistoryDraft);
        }
    }
}

/**
 * Detect language in text
 */
function detectLanguage(text) {
    // Count Tamil Unicode characters (U+0B80–U+0BFF)
    const tamilChars = (text.match(/[\u0B80-\u0BFF]/g) || []).length;
    const totalChars = text.length;
    const tamilPercentage = (tamilChars / totalChars) * 100;
    
    const hasEnglish = /[a-zA-Z]/.test(text);
    const hasTamil = tamilChars > 0;
    
    if (tamilPercentage > 20) {
        return 'ta';
    } else if (hasTamil && hasEnglish) {
        return 'tanglish';
    } else {
        return 'en';
    }
}

/**
 * Update language indicator
 */
function updateLanguageIndicator(language) {
    const labels = {
        'en': 'EN',
        'ta': 'தமிழ்',
        'tanglish': 'EN+தமிழ்'
    };
    // Guard: these elements don't exist in the current chatbot.html layout
    if (_languageText) _languageText.textContent = labels[language] || 'EN';
    if (_languageIndicator) _languageIndicator.setAttribute('data-lang', language);
}

/**
 * Send message to chatbot
 */
async function sendMessage() {
    const text = messageInput.value.trim();
    
    // Enhanced debugging
    console.log('=== SEND MESSAGE DEBUG ===');
    console.log('Text:', text);
    console.log('isTyping:', isTyping);
    console.log('currentSessionId:', currentSessionId);
    console.log('officerData:', officerData ? 'Present' : 'NULL');
    console.log('API_BASE_URL:', API_BASE_URL);
    
    if (!text) {
        console.warn('❌ Cannot send: No text');
        showToast('Please enter a message', 'warning');
        return;
    }

    if (text.toLowerCase() === 'attach test files' || text.toLowerCase() === 'load test files') {
        messageInput.value = '';
        handleInputChange();
        appendUserMessage(text);
        showToast('Attaching test files...', 'info');
        await window.uploadFileFromData("file_a.csv", "application_number,service_code,applicant_name\n2026/0153/01/0001,0153,Ramesh\n2026/0154/01/0002,0154,Suresh\n2026/0153/01/0003,0153,Mahesh");
        await window.uploadFileFromData("file_b.csv", "application_number,service_code,applicant_name\n2026/0154/01/0004,0154,Dinesh\n2026/0178/01/0005,0178,Ganesh");
        return;
    }
        console.warn('❌ Cannot send: Already typing');
        showToast('Please wait for the current response', 'warning');
        return;
    }
    
    if (!currentSessionId) {
        console.error('❌ Cannot send: No session ID');
        showToast(newChatInFlight ? 'Starting a new chat, please wait...'
                                  : 'No active chat session. Click "Start new conversation" to retry.', 'warning');
        return;
    }
    
    if (!officerData) {
        console.error('❌ Cannot send: No officer data');
        showToast('Authentication error. Please log in again.', 'error');
        setTimeout(() => { window.location.href = 'login.html'; }, 2000);
        return;
    }

    console.log('✓ All checks passed, sending message...');
    
    // Hide quick suggestions
    if (quickSuggestions) {
        quickSuggestions.style.display = 'none';
    }
    
    // Snapshot the conversation history BEFORE the current message is stored,
    // otherwise the backend prompt repeats this question in both the
    // CONVERSATION HISTORY block and the USER QUESTION block.
    const chatHistory = window.chatStorage ? window.chatStorage.getForAPI(CONTEXT_MESSAGE_LIMIT) : [];

    // Save user message to localStorage before rendering
    const userTimestamp = new Date().toISOString();
    if (window.chatStorage) {
        window.chatStorage.addMessage('user', text, detectLanguage(text));
    }
    
    // Render user message
    renderMessage('user', text, userTimestamp, detectLanguage(text));

    // The backend only sees the last CONTEXT_MESSAGE_LIMIT messages — once the
    // conversation grows past that, older turns are silently dropped. Warn only
    // when this message actually relies on them.
    maybeShowLongConversationNotice(text);
    
    // Clear input
    messageInput.value = '';
    messageInput.style.height = 'auto';
    inputHistoryIndex = -1; // next Up starts fresh from the message just sent
    inputHistoryDraft = '';
    isTyping = true;
    userStoppedResponse = false;
    setSendButtonState(true); // send button becomes the stop button
    
    // Show typing indicator
    showTypingIndicator();
    
    // Abort controller so we can cancel if the LLM hangs, or the user clicks Stop
    const controller = new AbortController();
    activeRequestController = controller;
    // This request's conversation. Once New Chat / "clear" bumps conversationGen
    // nothing below may write to the page, storage or state any more.
    const gen = conversationGen;
    const isStale = () => gen !== conversationGen;
    // 90 second hard timeout — LLMs can be slow but shouldn't run forever
    const timeoutId = setTimeout(() => {
        controller.abort();
        console.warn('sendMessage: stream timed out after 90s');
    }, 90000);
    
    // Hoisted so the catch block can finalize/save a partial response when the
    // user clicks Stop mid-stream, instead of discarding what already arrived.
    let messageDiv = null;
    let contentDiv = null;
    let aiResponse = '';
    let capturedTableData = null;
    // Set when the backend answers a typed "clear" with an instruction to wipe
    // the transcript. Declared out here so a stream that fails AFTER the
    // instruction arrived still carries the command out -- the officer asked
    // for a wipe, and leaving the transcript standing looks like the command
    // was ignored.
    let clearRequested = false;
    
    try {
        // chatHistory was snapshotted above, before the current message was stored
        console.log('=== CHAT HISTORY FOR CONTEXT ===');
        console.log(`📝 Including ${chatHistory.length} previous messages`);
        console.log('History:', JSON.stringify(chatHistory, null, 2));
        
        // Send to API
        const streamHeaders = { 'Content-Type': 'application/json' };
        if (officerData.access_token) {
            streamHeaders['Authorization'] = `Bearer ${officerData.access_token}`;
        }
        const response = await fetch(`${API_BASE_URL}/api/v1/chat/stream`, {
            method: 'POST',
            headers: streamHeaders,
            credentials: 'include',
            signal: controller.signal,
            body: JSON.stringify({
                message: text,
                session_id: currentSessionId,
                language: 'auto',
                chat_history: chatHistory
            })
        });
        
        console.log('✓ Request sent with chat history');
        if (isStale()) return;

        // Remove typing indicator now that the server has responded
        removeTypingIndicator();
        
        if (!response.ok) {
            const errText = await response.text().catch(() => '');
            throw new Error(`Server error ${response.status}: ${errText || 'Failed to get response'}`);
        }
        
        // Read SSE Stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        aiResponse = '';
        capturedTableData = null;  // capture table_data from SSE

        // Create an empty message div to stream into
        messageDiv = document.createElement('div');
        messageDiv.className = 'message message-assistant';
        const time = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        
        messageDiv.innerHTML = `
            <div class="message-avatar">
                <i data-lucide="bot" class="avatar-icon"></i>
            </div>
            <div class="message-content-wrapper">
                <div class="message-content" id="streaming-content">...</div>
                <div class="table-container-placeholder"></div>
                <div class="message-footer">
                    <span class="message-time">${time}</span>
                    <button class="btn-copy" onclick="copyMessage(this)" aria-label="Copy message">
                        <i data-lucide="copy" class="copy-icon"></i>
                    </button>
                </div>
            </div>
        `;
        chatMessages.appendChild(messageDiv);
        
        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
        
        contentDiv = messageDiv.querySelector('#streaming-content');
        
        // Read stream chunks until done
        let buffer = '';
        let chunkCount = 0;
        let streamComplete = false;   // the server's closing {"done": true} frame arrived
        // Acted on after the stream closes, never during it -- clearing
        // mid-stream would pull the DOM out from under the writer still
        // appending to it.
        
        while (true) {
            const { value, done } = await reader.read();
            if (isStale()) return;

            if (done) {
                // Stream complete
                break;
            }
            
            chunkCount++;
            
            // Accumulate partial chunks to handle split SSE frames
            const chunkText = decoder.decode(value, { stream: true });
            buffer += chunkText;
            
            if (chunkCount % 20 === 0) {
                console.log(`Chunk ${chunkCount}: received ${chunkText.length} bytes`);
            }
            
            // Process all complete SSE events (separated by \n\n)
            const events = buffer.split('\n\n');
            // Keep the last (possibly incomplete) event in the buffer
            buffer = events.pop();
            
            for (const event of events) {
                const line = event.trim();
                if (line.startsWith('data: ')) {
                    try {
                        const parsed = JSON.parse(line.slice(6));

                        // Capture table/structured data from first SSE event
                        if (parsed.table_data) {
                            capturedTableData = parsed.table_data;
                        } else if (parsed.structured_data && !capturedTableData) {
                            capturedTableData = parsed.structured_data;
                        }
                        if (parsed.action === 'clear_chat') {
                            clearRequested = true;
                        }
                        if (parsed.done) {
                            streamComplete = true;
                        }
                        if (parsed.content) {
                            aiResponse += parsed.content;
                            contentDiv.innerHTML = formatBotMessage(aiResponse.trimStart());
                            scrollToBottom();
                        }
                    } catch (e) {
                        console.warn('Could not parse SSE JSON:', line.substring(0, 100), e);
                    }
                }
            }
        }
        
        // Handle any remaining buffer content
        if (buffer.trim()) {
            const line = buffer.trim();
            if (line.startsWith('data: ')) {
                try {
                    const parsed = JSON.parse(line.slice(6));

                    if (parsed.table_data) capturedTableData = parsed.table_data;
                    else if (parsed.structured_data && !capturedTableData) capturedTableData = parsed.structured_data;
                    if (parsed.action === 'clear_chat') clearRequested = true;
                    if (parsed.done) streamComplete = true;
                    if (parsed.content) {
                        aiResponse += parsed.content;
                        contentDiv.innerHTML = formatBotMessage(aiResponse.trimStart());
                    }
                } catch (e) {
                    console.warn('Could not parse final SSE JSON:', e);
                }
            }
        }

        // No closing frame means the reply was cut off. Warn, and don't save the fragment.
        const interrupted = !!aiResponse && !streamComplete && !clearRequested;
        if (interrupted) {
            contentDiv.innerHTML += '<div style="color: orange; margin-top: 6px;">' +
                '⚠️ This reply was cut off. Please ask again.</div>';
        }

        // Check if we got any response
        if (!aiResponse && messageDiv && messageDiv.parentNode) {
            console.error('No content received from stream!');
            contentDiv.innerHTML = '<span style="color: orange;">⚠️ No response received. Please try again.</span>';
        }

        contentDiv.removeAttribute('id');

        // The officer typed "clear". Carry it out: wipe the transcript, drop
        // the stored history, open a fresh session. Done here rather than
        // saved-then-cleared, so the "clear" turn never reaches storage.
        if (clearRequested) {
            removeTypingIndicator();
            await handleNewChat({ toast: 'Conversation cleared' });
            return;
        }

        // Render table if structured data was captured during stream
        if (capturedTableData && typeof renderDataTable === 'function') {
            const placeholder = messageDiv.querySelector('.table-container-placeholder');
            if (placeholder) {
                const tableRenderArea = document.createElement('div');
                tableRenderArea.className = 'table-render-area';
                placeholder.appendChild(tableRenderArea);
                renderDataTable(tableRenderArea, capturedTableData);
                scrollToBottom();
            }
        }
        
        // Save assistant response to localStorage
        if (aiResponse && !interrupted) {
            if (window.chatStorage) {
                // Persist the table payload too, so tables survive a page refresh
                window.chatStorage.addMessage('assistant', aiResponse, 'auto', capturedTableData);
            }
            messageHistory = window.chatStorage ? window.chatStorage.load() : [];
            // Long conversation notice removed - backend handles context management automatically
        }

    } catch (error) {
        // Aborted (or failed) after the conversation was cleared: the new one
        // owns the page, the typing indicator and the toasts now.
        if (isStale()) return;
        removeTypingIndicator();
        if (clearRequested && !(error.name === 'AbortError' && userStoppedResponse)) {
            // The wipe instruction already arrived; the stream breaking
            // afterwards does not un-ask for it.
            console.warn('Stream failed after a clear instruction; clearing anyway:', error);
            await handleNewChat({ toast: 'Conversation cleared' });
            return;  // the finally block below still restores the input state
        }
        console.error('=== SEND MESSAGE ERROR ===');
        console.error('Error type:', error.name);
        console.error('Error message:', error.message);
        console.error('Full error:', error);
        
        if (error.name === 'AbortError' && userStoppedResponse) {
            // User clicked Stop — this is not a failure. Keep whatever text
            // already streamed in, mark it as stopped, and save it just like
            // a normal completed response.
            console.log('Response stopped by user');
            if (messageDiv && messageDiv.parentNode) {
                if (contentDiv) {
                    contentDiv.removeAttribute('id');
                    if (aiResponse) {
                        contentDiv.innerHTML = formatBotMessage(aiResponse.trimStart()) +
                            '<div class="stopped-notice">⏹ Response stopped</div>';
                    } else {
                        messageDiv.remove(); // nothing streamed in at all — drop the empty bubble
                    }
                }
                if (capturedTableData && aiResponse && typeof renderDataTable === 'function') {
                    const placeholder = messageDiv.querySelector('.table-container-placeholder');
                    if (placeholder) {
                        const tableRenderArea = document.createElement('div');
                        tableRenderArea.className = 'table-render-area';
                        placeholder.appendChild(tableRenderArea);
                        renderDataTable(tableRenderArea, capturedTableData);
                    }
                }
            }
            if (aiResponse && window.chatStorage) {
                window.chatStorage.addMessage('assistant', aiResponse, 'auto', capturedTableData);
                messageHistory = window.chatStorage.load();
            }
            showToast('Response stopped', 'info');
        } else if (error.name === 'AbortError') {
            console.error('Request timed out after 90 seconds');
            showToast('Response timed out. The AI may be busy — please try again.', 'warning');
        } else if (error.message.includes('Failed to fetch')) {
            console.error('Network error - cannot reach backend server');
            showToast('Cannot connect to server. Please check if the backend is running.', 'error');
        } else if (error.message.includes('401') || error.message.includes('403')) {
            console.error('Authentication error');
            showToast('Session expired. Please log in again.', 'error');
            setTimeout(() => {
                window.location.href = 'login.html';
            }, 2000);
        } else {
            console.error('Unknown error occurred');
            showToast(`Error: ${error.message}`, 'error');
        }
    } finally {
        clearTimeout(timeoutId);
        // Only the request that still owns the send state restores it; after a
        // New Chat that state was already reset and may belong to a newer request.
        if (activeRequestController === controller) {
            isTyping = false;
            activeRequestController = null;
            userStoppedResponse = false;
            setSendButtonState(false); // restore the normal send icon/behaviour
            handleInputChange();
        }
        console.log('=== SEND MESSAGE COMPLETE ===');
    }
}

/**
 * Handle Application Chip Click
 */
window.handleAppChipClick = function(element) {
    const appNumber = element.getAttribute('data-app') || element.innerText.trim();
    if (appNumber) {
        window.handleAppClick(appNumber);
    }
};

/**
 * Handle Application Click from tables, links, or text
 */
window.handleAppClick = function(appNumber) {
    if (!appNumber) return;
    const cleanApp = String(appNumber).trim();
    const query = `Show details for ${cleanApp}`;
    if (messageInput) {
        messageInput.value = query;
        handleInputChange();
        sendMessage();
    }
};

/**
 * Render message in chat
 */
function renderMessage(role, content, timestamp, language, showCopy = true, tableData = null) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message message-${role}`;
    
    // Format timestamp
    const time = new Date(timestamp).toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit'
    });
    
    if (role === 'assistant') {
        messageDiv.innerHTML = `
            <div class="message-avatar">
                <i data-lucide="bot" class="avatar-icon"></i>
            </div>
            <div class="message-content-wrapper">
                <div class="message-content">${formatBotMessage(content)}</div>
                <div class="table-container-placeholder"></div>
                <div class="message-footer">
                    <span class="message-time">${time}</span>
                    ${showCopy ? `<button class="btn-copy" onclick="copyMessage(this)" aria-label="Copy message">
                        <i data-lucide="copy" class="copy-icon"></i>
                    </button>` : ''}
                </div>
            </div>
        `;
        
        // Append table if tableData is not null
        if (tableData) {
            const placeholder = messageDiv.querySelector('.table-container-placeholder');
            if (placeholder) {
                const tableRenderArea = document.createElement('div');
                tableRenderArea.className = 'table-render-area';
                placeholder.appendChild(tableRenderArea);
                if (typeof renderDataTable === 'function') {
                    renderDataTable(tableRenderArea, tableData);
                }
            }
        }
    } else {
        messageDiv.innerHTML = `
            <div class="message-content-wrapper">
                <div class="message-content">${escapeHtml(content)}</div>
                <div class="message-footer">
                    <span class="message-time">${time}</span>
                </div>
            </div>
        `;
    }
    
    chatMessages.appendChild(messageDiv);
    
    // Reinitialize Lucide icons
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }
    
    // Scroll to bottom
    scrollToBottom();
    
    // Add to history
    messageHistory.push({ role, content, timestamp, language, tableData });
}

/**
 * Append message wrapper to support requested function signature
 */
function appendMessage(role, content, timestamp, tableData = null) {
    renderMessage(role, content, timestamp, 'auto', true, tableData);
}

/**
 * Format bot message for display.
 *
 * Backend sends two kinds of responses:
 *   1. Clean HTML  — built by build_html_response() in rag.py (tables, lists, etc.)
 *   2. Plain text  — LLM free-text answer for general queries
 *
 * HTML responses are injected directly via innerHTML (trusted backend source).
 * Plain text responses get light markdown conversion before injection.
 */
function renderMarkdownTables(text) {
    if (!text || !text.includes('|')) return text;
    
    const lines = text.split(/\r?\n/);
    const out = [];
    let tableLines = [];
    let inTable = false;

    function formatTable(tbl) {
        if (tbl.length < 2) return tbl.join('\n');
        const headerLine = tbl[0];
        const delimLine = tbl[1];
        if (!delimLine.includes('-')) return tbl.join('\n');

        const parseCells = (line) => {
            const trimmed = line.trim();
            const inner = trimmed.replace(/^\|/, '').replace(/\|$/, '');
            return inner.split('|').map(c => c.trim());
        };

        const headers = parseCells(headerLine);
        const dataRows = tbl.slice(2).map(parseCells).filter(r => r.length > 0 && r.some(c => c !== ''));

        let html = '<div class="data-table-container"><table class="data-table"><thead><tr>';
        headers.forEach(h => {
            let cell = escapeHtml(h);
            cell = cell.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
            html += `<th>${cell}</th>`;
        });
        html += '</tr></thead><tbody>';
        dataRows.forEach(row => {
            html += '<tr>';
            for (let i = 0; i < headers.length; i++) {
                let cell = escapeHtml(row[i] !== undefined ? row[i] : '');
                cell = cell.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
                cell = cell.replace(/`([^`]+)`/g, '<code>$1</code>');
                html += `<td>${cell}</td>`;
            }
            html += '</tr>';
        });
        html += '</tbody></table></div>';
        return html;
    }

    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const isTableLine = /^\s*\|.*\|\s*$/.test(line);
        if (isTableLine) {
            inTable = true;
            tableLines.push(line);
        } else {
            if (inTable) {
                out.push(formatTable(tableLines));
                tableLines = [];
                inTable = false;
            }
            out.push(line);
        }
    }
    if (inTable) {
        out.push(formatTable(tableLines));
    }
    return out.join('\n');
}

function formatMarkdownText(raw) {
    if (!raw) return '';
    const lines = raw.split(/\r?\n/);
    const processedLines = lines.map(line => {
        // Markdown headings
        if (/^###\s+/.test(line)) {
            return `<h3 style="font-size: 14px; font-weight: 600; margin: 10px 0 4px 0; color: var(--text-primary);">${escapeHtml(line.replace(/^###\s+/, ''))}</h3>`;
        }
        if (/^##\s+/.test(line)) {
            return `<h2 style="font-size: 15px; font-weight: 700; margin: 12px 0 6px 0; color: var(--text-primary);">${escapeHtml(line.replace(/^##\s+/, ''))}</h2>`;
        }
        if (/^#\s+/.test(line)) {
            return `<h1 style="font-size: 17px; font-weight: 700; margin: 14px 0 6px 0; color: var(--text-primary);">${escapeHtml(line.replace(/^#\s+/, ''))}</h1>`;
        }
        let esc = escapeHtml(line);
        esc = esc.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        esc = esc.replace(/`([^`]+)`/g, '<code>$1</code>');
        return esc;
    });

    let formatted = processedLines.join('\n');
    formatted = formatted.replace(/\n{3,}/g, '\n\n');
    // Bullet lists: lines starting with •, -, or *
    formatted = formatted.replace(/((?:^[•\-\*]\s+.+(?:\n|$))+)/gm, m => '<ul>' + m.trim().split('\n').map(l => '<li>' + l.replace(/^[•\-\*]\s+/, '') + '</li>').join('') + '</ul>');
    // Numbered lists: lines starting with "1. "
    formatted = formatted.replace(/((?:^\d+\.\s+.+(?:\n|$))+)/gm, m => '<ol>' + m.trim().split('\n').map(l => '<li>' + l.replace(/^\d+\.\s+/, '') + '</li>').join('') + '</ol>');
    
    // Collapse newlines to <br> for prose, avoiding extraneous breaks around block tags
    formatted = formatted.replace(/\n{2,}/g, '<br><br>');
    formatted = formatted.replace(/\n/g, '<br>');
    formatted = formatted.replace(/<\/h([1-3])><br>/gi, '</h$1>');
    formatted = formatted.replace(/<\/ul><br>/gi, '</ul>');
    formatted = formatted.replace(/<\/ol><br>/gi, '</ol>');

    // Application number chips
    formatted = formatted.replace(
        /\b((?:ISD|NISD)\/\w+\/\d+\/\d+)\b/gi,
        '<span class="suggestion-chip" data-app="$1" onclick="handleAppChipClick(this)" ' +
        'style="cursor:pointer;display:inline-block;margin:2px;">$1</span>'
    );

    return formatted;
}

/**
 * Format bot message for display.
 *
 * Backend sends two kinds of responses:
 *   1. Clean HTML  — built by build_html_response() in rag.py (tables, lists, etc.)
 *   2. Plain text / Markdown — LLM or deterministic table/prose answers
 *
 * HTML responses are injected directly via innerHTML (trusted backend source).
 * Plain text / Markdown responses get structured markdown table and block conversion before injection.
 */
function formatBotMessage(text) {
    if (!text) return '';
    // Detect HTML: any opening tag at all (e.g. <table, <p, <ul, <strong …)
    const hasHTML = /<[a-zA-Z][^>]*>/i.test(text);
    if (hasHTML) {
        // Trusted HTML from the backend — inject as-is.
        return text.trim();
    }
    
    // ── Structured plain text (bullets, "Label: value" lines) → the same card the details use ──
    const card = plainToCard(text);
    if (card) return card;

    // First render Markdown tables if any table syntax exists
    const formattedText = renderMarkdownTables(text);

    // If formattedText contains rendered table containers, split into segments
    // so we don't break the generated table HTML
    if (formattedText.includes('<div class="data-table-container">')) {
        const parts = formattedText.split(/(<div class="data-table-container">[\s\S]*?<\/div>)/);
        return parts.map(part => {
            if (part.startsWith('<div class="data-table-container">')) {
                return part;
            }
            return formatMarkdownText(part);
        }).join('');
    }

    return formatMarkdownText(formattedText);
}

/**
 * A plain-text answer made of bullets / "Label: value" lines (the record, visit-plan and "not held" answers) is shown
 * as the same titled FIELD | DETAILS card as the ISD / NISD details, so every answer looks alike. Prose is left alone.
 * Returns null when the text is not that shape.
 */
function plainToCard(text) {
    const lines = text.split('\n').map(l => l.replace(/\s+$/, '')).filter(l => l.trim());
    if (lines.length < 3) return null;
    const isBullet = l => /^\s*[•\-–]\s+/.test(l);
    const labelRe = /^(.{1,70}?)\s*:\s+(.+)$/;
    const appRe = /^(\d{4}\/\d{3,4}\/\d{1,3}\/\d+)\b\s*(.*)$/;
    const bullets = lines.filter(isBullet).length;
    const labelled = lines.filter(l => labelRe.test(l.replace(/^\s*[•\-–]\s+/, ''))).length;
    if (bullets < 2 && labelled < 3) return null;
    // an intro, a plain bullet list and a closing sentence is a message with a list, not a record
    if (labelled < 2 && !isBullet(lines[lines.length - 1]) && !lines.some(l => appRe.test(l.replace(/^\s*[•\-–]\s+/, '')))) return null;
    if (lines.some(l => !isBullet(l) && l.length > 160)) return null;          // prose, not a record
    let title = null;
    const rows = [];
    lines.forEach((raw, i) => {
        const bullet = isBullet(raw);
        const body = raw.replace(/^\s*[•\-–]\s+/, '').trim();
        if (title === null && i === 0 && !bullet) { title = body.replace(/:$/, ''); return; }
        const step = body.match(/^(\d{1,2})[.)]\s+(.+)$/);
        const app = body.match(appRe);
        const lab = body.match(labelRe);
        if (!bullet && /:$/.test(body)) rows.push(['section', body.replace(/:$/, ''), '']);
        else if (step) rows.push(['row', step[1], step[2]]);
        else if (app && app[2]) rows.push(['row', app[1], app[2].replace(/^[—–-]\s*/, '')]);
        else if (lab && !/^(Note|\()/.test(body)) rows.push(['row', lab[1], lab[2]]);
        else rows.push(['note', body, '']);
    });
    if (!rows.length) return null;
    const n = rows.filter(r => r[0] === 'row').length;
    const body = rows.map(r => r[0] === 'section'
        ? `<tr><td colspan="2"><strong>${escapeHtml(r[1])}</strong></td></tr>`
        : r[0] === 'note'
            ? `<tr><td colspan="2">${escapeHtml(r[1])}</td></tr>`
            : `<tr><td>${escapeHtml(r[1])}</td><td>${escapeHtml(r[2])}</td></tr>`).join('');
    const count = n ? `<span class="data-table-count">${n} records</span>` : '';
    return `<div class="data-table-card"><div class="data-table-header"><div class="data-table-title-section">` +
        `<span class="data-table-icon">📋</span><h3 class="data-table-title">${escapeHtml(title || '')}</h3>${count}</div>` +
        `<button class="btn-copy-table" onclick="window.copyTableData(this)">Copy</button></div>` +
        `<div class="data-table-container"><table class="data-table"><thead><tr><th>${rows.filter(r => r[0] === 'row' && appRe.test(r[1])).length * 2 > n ? 'APPLICATION' : 'FIELD'}</th><th>DETAILS</th></tr></thead>` +
        `<tbody class="table-body-rows">${body}</tbody></table></div></div>`;
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Show typing indicator with thinking animation
 */
let thinkingTextInterval = null;
const thinkingMessages = [
    'Thinking...',
    'யோசிக்கிறேன்...',
    'Analyzing your question...',
    'உங்கள் கேள்வியை ஆராய்கிறேன்...',
    'Searching knowledge base...',
    'தகவல்களைத் தேடுகிறேன்...',
    'Processing...',
    'செயலாக்குகிறேன்...',
    'Preparing response...',
    'பதிலை தயாரிக்கிறேன்...'
];
let thinkingMessageIndex = 0;

function showTypingIndicator() {
    const indicator = document.createElement('div');
    indicator.className = 'message message-assistant typing-indicator';
    indicator.id = 'typingIndicator';
    indicator.innerHTML = `
        <div class="message-avatar">
            <i data-lucide="bot" class="avatar-icon"></i>
        </div>
        <div class="message-content-wrapper">
            <div class="typing-dots">
                <span class="dot"></span>
                <span class="dot"></span>
                <span class="dot"></span>
            </div>
            <span class="typing-text" id="thinkingText">${thinkingMessages[0]}</span>
        </div>
    `;
    
    chatMessages.appendChild(indicator);
    
    // Reinitialize Lucide icons
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }
    
    scrollToBottom();
    
    // Cycle through thinking messages every 2 seconds with fade effect
    thinkingMessageIndex = 0;
    thinkingTextInterval = setInterval(() => {
        const textEl = document.getElementById('thinkingText');
        if (textEl) {
            // Fade out
            textEl.style.opacity = '0';
            
            // Change text after fade out
            setTimeout(() => {
                thinkingMessageIndex = (thinkingMessageIndex + 1) % thinkingMessages.length;
                textEl.textContent = thinkingMessages[thinkingMessageIndex];
                // Fade in
                textEl.style.opacity = '1';
            }, 150);
        } else {
            // Element removed, clear interval
            if (thinkingTextInterval) {
                clearInterval(thinkingTextInterval);
                thinkingTextInterval = null;
            }
        }
    }, 2000);
}

/**
 * Remove typing indicator
 */
function removeTypingIndicator() {
    // Clear the thinking text interval
    if (thinkingTextInterval) {
        clearInterval(thinkingTextInterval);
        thinkingTextInterval = null;
    }
    
    const indicator = document.getElementById('typingIndicator');
    if (indicator) {
        indicator.remove();
    }
}

/**
 * Scroll chat to bottom
 */
function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

/**
 * Copy message to clipboard
 */
window.copyMessage = function(button) {
    const messageContent = button.closest('.message-content-wrapper').querySelector('.message-content');
    const text = messageContent.innerText;
    
    navigator.clipboard.writeText(text).then(() => {
        showToast('Copied to clipboard!', 'success');
        
        // Change icon temporarily
        const icon = button.querySelector('.copy-icon');
        icon.setAttribute('data-lucide', 'check');
        lucide.createIcons();
        
        setTimeout(() => {
            icon.setAttribute('data-lucide', 'copy');
            lucide.createIcons();
        }, 2000);
    }).catch(err => {
        console.error('Copy failed:', err);
        showToast('Failed to copy', 'error');
    });
};

/**
 * Load session history
 */
async function loadSessionHistory() {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000); // 3 second timeout
    
    try {
        const response = await fetch(`${API_BASE_URL}/api/v1/chat/sessions`, {
            credentials: 'include',  // Cookie sent automatically
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            throw new Error('Failed to load sessions');
        }
        
        const data = await response.json();
        renderSessionHistory(data.data.sessions);
        
    } catch (error) {
        clearTimeout(timeoutId);
        console.log('Could not load session history:', error.message);
        // Don't throw - this is non-critical
    }
}

/**
 * Render session history in sidebar
 */
function renderSessionHistory(sessions) {
    if (!sessionHistoryList) return;
    
    sessionHistoryList.innerHTML = '';
    
    if (!sessions || sessions.length === 0) {
        sessionHistoryList.innerHTML = '<p class="empty-history">No previous chats</p>';
        return;
    }
    
    // Show last 10 sessions
    sessions.slice(0, 10).forEach(session => {
        const item = document.createElement('div');
        item.className = 'session-item';
        if (session.session_id === currentSessionId) {
            item.classList.add('active');
        }
        
        const date = new Date(session.started_at).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric'
        });
        
        item.innerHTML = `
            <div class="session-info">
                <span class="session-title">Chat Session</span>
                <span class="session-date">${date}</span>
            </div>
        `;
        
        item.addEventListener('click', () => {
            // Load this session (future enhancement)
            showToast('Loading previous sessions coming soon', 'info');
        });
        
        sessionHistoryList.appendChild(item);
    });
}

/**
 * Handle new chat
 */
function handleNewChat(options) {
    // A second click (or a "clear" typed meanwhile) joins the transition
    // already running instead of opening a second session.
    if (!newChatInFlight) {
        newChatInFlight = startNewChat(options).finally(() => { newChatInFlight = null; });
    }
    return newChatInFlight;
}

async function startNewChat(options) {
    // The old session's attachment list belongs to a conversation that is
    // about to stop existing -- close the panel rather than let it keep
    // showing files from a session the officer just left.
    closeAttachmentsPanel();
    // Retire the old conversation first: its in-flight answer is cancelled and,
    // through conversationGen, can no longer write to the page or storage.
    conversationGen++;
    if (activeRequestController) activeRequestController.abort();
    activeRequestController = null;
    isTyping = false;
    userStoppedResponse = false;
    removeTypingIndicator();
    // No session until the server confirms a new one, so nothing can be sent
    // to the old one in the meantime.
    currentSessionId = null;
    setSendButtonState(false);

    chatMessages.innerHTML = '';
    messageHistory = [];
    inputHistoryIndex = -1;
    inputHistoryDraft = '';
    longConversationNoticeShown = false;

    if (window.chatStorage) {
        window.chatStorage.clear();
    }

    try {
        if (!await createNewSession()) return;  // superseded by a newer attempt
    } catch (error) {
        console.error('Could not start a new chat session:', error);
        appendAssistantNotice('<strong>Could not start a new chat.</strong> ' +
            escapeHtml(error.message || 'Unknown error') +
            '<br>Nothing can be sent until a new chat is started.', true);
        showToast('Could not start a new chat. Please retry.', 'error');
        return;
    } finally {
        handleInputChange();
    }

    if (window.chatStorage) {
        window.chatStorage.saveSessionId(currentSessionId);
    }

    renderWelcomeMessage();
    refreshAttachmentsPanel();  // resets the badge to the new, empty session

    if (quickSuggestions) {
        quickSuggestions.style.display = 'block';
    }
    
    // A typed "clear" says what it did in its own words; the button says
    // "New chat started". Same wipe, two names for it.
    showToast((options && options.toast) || 'New chat started', 'success');
}

/**
 * Handle logout
 */
async function handleLogout() {
    try {
        console.log('🚪 Logging out...');
        
        // Clear chat history and session ID from localStorage (must happen
        // BEFORE officer_data is removed so the scoped key can still be built)
        if (window.chatStorage) {
            window.chatStorage.clear();
            console.log('✓ Cleared chat history from localStorage');
        }
        
        // Clear officer data from sessionStorage
        sessionStorage.removeItem('officer_data');
        
        // Remove the persisted officer id so a different user on the same
        // browser starts fresh (chatStorage.clear above ran while officer_data
        // was still set, so it already removed the scoped history keys)
        localStorage.removeItem('sis_last_officer_id');
        
        console.log('✓ All session data cleared');
        
        // Redirect to login
        window.location.href = 'login.html';
    } catch (error) {
        console.error('Logout error:', error);
        // Still try to redirect even if clearing fails
        window.location.href = 'login.html';
    }
}

/**
 * Toggle mobile sidebar
 */
function toggleMobileSidebar() {
    if (chatSidebar) {
        chatSidebar.classList.toggle('mobile-open');
    }
}

/**
 * Show toast notification
 */
function showToast(message, type = 'info') {
    const toastContainer = document.getElementById('toastContainer');
    if (!toastContainer) return;
    
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    const icons = {
        success: 'check-circle',
        error: 'alert-circle',
        warning: 'alert-triangle',
        info: 'info'
    };
    
    toast.innerHTML = `
        <i data-lucide="${icons[type]}" class="toast-icon"></i>
        <span class="toast-message">${message}</span>
    `;
    
    toastContainer.appendChild(toast);
    
    // Reinitialize icons
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }
    
    // Slide in
    setTimeout(() => {
        toast.classList.add('show');
    }, 10);
    
    // Auto dismiss after 3 seconds
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => {
            toast.remove();
        }, 300);
    }, 3000);
}

// Export for external use
window.chatModule = {
    sendMessage,
    copyMessage,
    showToast
};

/**
 * Handle file attachment button
 */
async function processAndUploadFile(file) {
    if (!file) return;

    // Validate file size (max 5MB)
    const maxSize = 5 * 1024 * 1024;
    if (file.size > maxSize) {
        showToast('File size must be less than 5MB', 'error');
        return;
    }

    const ext = (file.name.split('.').pop() || '').toLowerCase();
    const isReadable  = ['txt', 'csv', 'pdf', 'docx'].includes(ext);
    const isLegacyDoc = ext === 'doc';

    // Images and everything else are not accepted — the assistant has no
    // vision and can only work from text.
    if (!isReadable && !isLegacyDoc) {
        showToast('Unsupported file type. Upload a .txt, .csv, PDF or .docx file.', 'error');
        return;
    }

    const fileAnalysis = await analyzeFile(file);
    showFilePreview(file, fileAnalysis);

    if (isReadable) {
        // Send the file to the backend, which extracts the text and keeps it
        // for this chat session so every later question can use it.
        showToast('Uploading file…', 'info');
        try {
            const result = await uploadFile(file);
            const data = result && result.data;
            if (data && data.supported === false) {
                appendAssistantNotice(`📄 <strong>${escapeHtml(file.name)}</strong> — ${escapeHtml(result.message || 'not supported')}`);
            } else if (data && data.extraction_status === 'no_extractable_text') {
                // A scan. There is no OCR and no vision here, so the file is
                // recorded but never answered from — say so instead of
                // letting the officer ask questions it cannot answer.
                appendAssistantNotice(
                    `📄 <strong>${escapeHtml(file.name)}</strong> — ${escapeHtml(result.message || 'no readable text found')}`);
            } else {
                const msg = (result && result.message) ||
                    `"${file.name}" attached. Ask your question about it now.`;
                appendAssistantNotice(`📎 <strong>${escapeHtml(file.name)}</strong> attached.<br>${escapeHtml(msg)}`);
            }
            return result;
        } catch (e) {
            const why = e && e.detail ? ` ${escapeHtml(e.detail)}` : '';
            appendAssistantNotice(
                `⚠️ Could not attach <strong>${escapeHtml(file.name)}</strong>.${why}<br>` +
                `Please paste the relevant text into the chat instead.`);
        }
    } else {
        // .doc — legacy binary format, no reader available
        appendAssistantNotice(
            `📄 <strong>${escapeHtml(file.name)}</strong> — the legacy <strong>.doc</strong> format can't be read. ` +
            `Save it as <strong>.docx</strong> or PDF, or paste the text into the chat.`);
    }
}

function handleFileAttachment() {
    let fileInput = document.getElementById('fileUploadInput');
    if (!fileInput) {
        fileInput = document.createElement('input');
        fileInput.id = 'fileUploadInput';
        fileInput.type = 'file';
        fileInput.accept = '.txt,.csv,.pdf,.doc,.docx';
        fileInput.style.display = 'none';
        document.body.appendChild(fileInput);
    }

    fileInput.onchange = async (event) => {
        const file = event.target.files && event.target.files[0];
        if (file) {
            await processAndUploadFile(file);
        }
        fileInput.value = '';
    };

    fileInput.click();
}

window.uploadFileFromData = async function(fileName, textContent, mimeType = 'text/csv') {
    const blob = new Blob([textContent], { type: mimeType });
    const file = new File([blob], fileName, { type: mimeType });
    return await processAndUploadFile(file);
};

// Phrases by which an officer points back at something discussed earlier. Kept
// in step with _REFERENCE_PATTERNS in backend/services/chatbot.py, plus the
// wording used for lists ("the ones above", "same ward"). Matched on whole-word
// boundaries so "the app" does not fire inside "the applicant".
const BACK_REFERENCE_PATTERNS = [
    'this application', 'that application', 'same application',
    'this app', 'that app', 'the application', 'the app',
    'prev application', 'previous application', 'prev app', 'previous app',
    'last application', 'last app', 'above application', 'overdue application',
    'it', 'its', 'this one', 'that one', 'the same one', 'previous one', 'same one',
    'these', 'those', 'them', 'the ones', 'the above', 'above', 'earlier',
    'same', 'again', 'first one', 'second one', 'last one',
    'இந்த விண்ணப்பம்', 'அந்த விண்ணப்பம்', 'முந்தைய விண்ணப்பம்',
    'இதன்', 'அதன்', 'இது', 'அது', 'இதே',
    'adhoda', 'idhoda', 'adhu', 'idhu', 'adhe', 'idhe'
];

const BACK_REFERENCE_RE = new RegExp(
    '(?:^|[^\\w\\u0B80-\\u0BFF])(?:' +
    BACK_REFERENCE_PATTERNS.map(p => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') +
    ')(?![\\w\\u0B80-\\u0BFF])',
    'i'
);

/**
 * True when the message leans on the conversation instead of standing alone —
 * either an explicit back-reference ("what is its status") or a bare fragment
 * that only makes sense as a follow-up filter ("only isd", "only pending").
 */
function messageNeedsPriorContext(text) {
    const msg = (text || '').trim();
    if (!msg) return false;
    if (BACK_REFERENCE_RE.test(msg)) return true;
    // A bare fragment like "only isd" / "only pending" is a follow-up filter on
    // the previous answer. A short *question or command* ("show pending",
    // "list isd apps") stands on its own, so it is not one.
    const words = msg.split(/\s+/);
    const startsAQuery = /^(show|list|give|display|get|find|fetch|count|how|what|which|who|when|where|why|is|are|do|does|can|tell|check|status|search|open|view)\b/i.test(msg);
    if (words.length <= 3 && !startsAQuery && !/\d/.test(msg) && !/\?/.test(msg)) return true;
    return false;
}

/**
 * The backend is only sent the last CONTEXT_MESSAGE_LIMIT messages, so in a long
 * conversation the earlier turns are dropped and answers stop being able to use
 * them. That only matters when the officer is actually referring back to
 * something, so the notice fires on a context-dependent message — not on every
 * self-contained question asked after the 10th message.
 */
function maybeShowLongConversationNotice(text) {
    if (longConversationNoticeShown) return;
    // "clear" / "new chat" ends the conversation -- warning that it is long, then wiping it, is noise.
    if (/^\s*(?:please\s+)?(?:clear|cls|reset|wipe|erase|delete|remove|flush|clean|new\s+chat|start\s+(?:a\s+)?new)\b|\b(?:clear|wipe|erase)\b.*\b(?:chat|conversation|history)\b|^\s*அழி/i.test(text || '')) return;

    const stored = (window.chatStorage ? window.chatStorage.load() : messageHistory) || [];
    // stored already includes the message just sent.
    if (stored.length <= CONTEXT_MESSAGE_LIMIT) return;

    // Self-contained question — the dropped turns change nothing about the
    // answer, so there is nothing to warn about.
    if (!messageNeedsPriorContext(text)) return;

    // Only warn when the thing being referred to is really out of reach: the
    // last CONTEXT_MESSAGE_LIMIT messages the backend sees carry no application
    // number, but the dropped older ones do. If the reference still resolves
    // inside the window, the long conversation costs the officer nothing.
    const appNo = /\b\d{4}\/\d{3,4}\/\d{2}\/\d{4,6}\b/;
    const visible = stored.slice(-(CONTEXT_MESSAGE_LIMIT + 1), -1);
    const older = stored.slice(0, -(CONTEXT_MESSAGE_LIMIT + 1));
    if (visible.some(m => appNo.test(m.content || ''))) return;
    if (!older.some(m => appNo.test(m.content || ''))) return;

    const userTurns = stored.filter(m => m.role === 'user').length;
    longConversationNoticeShown = true;

    const dropped = stored.length - CONTEXT_MESSAGE_LIMIT;
    appendAssistantNotice(
        `⚠️ <strong>This conversation has passed the context limit.</strong><br>` +
        `You are referring back to something earlier, but I only keep the last ` +
        `${CONTEXT_MESSAGE_LIMIT} messages in view — ${dropped} earlier ` +
        `message${dropped === 1 ? '' : 's'} from this chat (${userTurns} of yours in total) can no longer be used. ` +
        `If this answer looks off, start a new conversation and give the application or survey number again.`,
        true
    );
    showToast('Conversation is past the context limit — consider starting a new chat', 'warning');
}

/**
 * Append a short assistant-style notice bubble (not persisted to history).
 */
function appendAssistantNotice(html, withNewChatAction = false) {
    const div = document.createElement('div');
    div.className = 'message message-assistant';
    const time = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    const action = withNewChatAction
        ? `<div class="notice-actions"><button type="button" class="notice-new-chat-btn">Start new conversation</button></div>`
        : '';
    div.innerHTML = `
        <div class="message-avatar"><i data-lucide="bot" class="avatar-icon"></i></div>
        <div class="message-content-wrapper">
            <div class="message-content">${html}${action}</div>
            <div class="message-footer"><span class="message-time">${time}</span></div>
        </div>`;
    if (withNewChatAction) {
        const btn = div.querySelector('.notice-new-chat-btn');
        if (btn) btn.addEventListener('click', () => handleNewChat());
    }
    chatMessages.appendChild(div);
    if (typeof lucide !== 'undefined') lucide.createIcons();
    scrollToBottom();
}

/**
 * Analyze uploaded file
 */
async function analyzeFile(file) {
    const analysis = {
        name: file.name,
        type: file.type,
        size: file.size,
        sizeFormatted: formatFileSize(file.size),
        extension: file.name.split('.').pop().toLowerCase(),
        canAnalyze: false,
        preview: null,
        metadata: {}
    };

    // Text / CSV file analysis
    if (file.type === 'text/plain' || analysis.extension === 'txt' || analysis.extension === 'csv') {
        analysis.canAnalyze = true;
        analysis.category = analysis.extension === 'csv' ? 'csv' : 'text';

        try {
            const content = await readFileAsText(file);
            analysis.metadata = {
                lines: content.split('\n').length,
                words: content.split(/\s+/).filter(w => w.length > 0).length,
                characters: content.length,
                preview: content.substring(0, 200)
            };
        } catch (error) {
            console.error('Error analyzing text file:', error);
        }
    }
    
    // PDF
    else if (file.type === 'application/pdf' || analysis.extension === 'pdf') {
        analysis.canAnalyze = true;
        analysis.category = 'pdf';
        analysis.metadata = { info: 'PDF — text extracted on upload' };
    }

    // Word
    else if (analysis.extension === 'docx' || analysis.extension === 'doc' ||
             file.type.includes('word') || file.type.includes('document')) {
        analysis.canAnalyze = true;
        analysis.category = 'document';
        analysis.metadata = { info: 'Word document — text extracted on upload' };
    }

    return analysis;
}

/**
 * Format file size
 */
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}

/**
 * Read file as text
 */
function readFileAsText(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => resolve(e.target.result);
        reader.onerror = (e) => reject(e);
        reader.readAsText(file);
    });
}

/**
 * Show file preview in chat with analysis
 */
function showFilePreview(file, analysis) {
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message message-user';
    const time = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    
    let previewContent = `<div class="file-attachment">
        <div class="file-header">
            <strong>📎 ${escapeHtml(file.name)}</strong>
            <span class="file-size">${analysis.sizeFormatted}</span>
        </div>`;
    
    // Add metadata
    if (Object.keys(analysis.metadata).length > 0) {
        previewContent += '<div class="file-metadata">';
        if (analysis.category === 'text' || analysis.category === 'csv') {
            previewContent += `
                <div>📄 ${analysis.metadata.lines} lines</div>
                <div>📊 ${analysis.metadata.words} words</div>
            `;
        }
        previewContent += '</div>';
    }

    previewContent += '</div>';

    messageDiv.innerHTML = `
        <div class="message-content-wrapper">
            <div class="message-content file-content">${previewContent}</div>
            <div class="message-footer">
                <span class="message-time">${time}</span>
            </div>
        </div>
    `;

    chatMessages.appendChild(messageDiv);

    scrollToBottom();
    
    showToast('✓ File analyzed successfully', 'success');
}

/**
 * Upload file to server (placeholder for future implementation)
 */
async function uploadFile(file) {
    if (!currentSessionId) {
        const err = new Error('No active chat session');
        err.detail = 'No active chat session. Start a new conversation and try again.';
        throw err;
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('session_id', currentSessionId);

    const headers = {};
    if (officerData && officerData.access_token) {
        headers['Authorization'] = `Bearer ${officerData.access_token}`;
    }

    const response = await fetch(`${API_BASE_URL}/api/v1/chat/upload`, {
        method: 'POST',
        headers,
        credentials: 'include',
        body: formData
    });

    if (!response.ok) {
        let detail = '';
        try { detail = (await response.json()).detail || ''; }
        catch (_) { detail = await response.text().catch(() => ''); }
        console.error('File upload error:', response.status, detail);
        const err = new Error(detail || `Upload failed (${response.status})`);
        err.status = response.status;
        err.detail = detail;
        throw err;
    }

    // Backend returns a StandardResponse: { success, data, message, timestamp }
    const body = await response.json();
    if (body && body.data && body.data.supported === false) {
        showToast('This file type is not supported yet', 'info');
    } else {
        showToast('File attached', 'success');
    }
    refreshAttachmentsPanel();
    return body;
}

// ─────────────────────────────────────────────────────────────────────────
// Attachment management panel -- server state only. Every render re-fetches
// GET /sessions/{id}/attachments; nothing here is read from localStorage,
// because a cached list surviving from before a restart/expiry would read
// as proof a document is still queryable when the server may have already
// expired, retired or deleted it.
// ─────────────────────────────────────────────────────────────────────────

const ATTACHMENT_LIFECYCLE_LABEL = {
    active: 'Active',
    expired: 'Expired',
    retired: 'Retired',
    failed: 'Extraction failed',
    no_extractable_text: 'No readable text',
};

function _authHeaders() {
    return officerData && officerData.access_token
        ? { 'Authorization': `Bearer ${officerData.access_token}` } : {};
}

// The header's "Online" dot used to be static markup -- true regardless of
// whether the backend was actually reachable. It now reflects a real check
// against GET /health (no auth needed, so this still works even if the
// officer's token has expired) on load and every 30s thereafter.
let _connectionCheckTimer = null;

async function _checkConnection() {
    const dot = document.querySelector('.status-dot-new');
    const text = document.querySelector('.status-text-new');
    if (!dot || !text) return;
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);
        const r = await fetch(`${API_BASE_URL}/health`, { signal: controller.signal });
        clearTimeout(timeout);
        if (r.ok) {
            dot.className = 'status-dot-new';
            text.textContent = 'Online';
            return;
        }
    } catch (e) { /* network error or timeout -- treated as offline below */ }
    dot.className = 'status-dot-new status-dot-offline';
    text.textContent = 'Offline';
}

function initConnectionStatus() {
    _checkConnection();
    if (_connectionCheckTimer) clearInterval(_connectionCheckTimer);
    _connectionCheckTimer = setInterval(_checkConnection, 30000);
    window.addEventListener('online', _checkConnection);
    window.addEventListener('offline', _checkConnection);
}

async function fetchAttachments() {
    if (!currentSessionId) return { attachments: [], count: 0, max_files: 0 };
    const r = await fetch(`${API_BASE_URL}/api/v1/chat/sessions/${currentSessionId}/attachments`,
                         { credentials: 'include', headers: _authHeaders() });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const body = await r.json();
    return (body && body.data) || { attachments: [], count: 0, max_files: 0 };
}

async function fetchAttachmentPreview(documentId) {
    if (!currentSessionId) throw new Error('No active chat session');
    const r = await fetch(
        `${API_BASE_URL}/api/v1/chat/sessions/${currentSessionId}/attachments/${documentId}/preview`,
        { credentials: 'include', headers: _authHeaders() });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const body = await r.json();
    return (body && body.data) || { text: '' };
}

function _formatExpiry(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const mins = Math.round((d.getTime() - now.getTime()) / 60000);
    if (mins <= 0) return 'expired';
    if (mins < 60) return `expires in ${mins} min`;
    return `expires ${d.toLocaleString()}`;
}

function _renderAttachmentCard(doc) {
    const statusClass = `attachment-status-${doc.lifecycle_status}`;
    const statusLabel = ATTACHMENT_LIFECYCLE_LABEL[doc.lifecycle_status] || doc.lifecycle_status;
    const needsReupload = doc.lifecycle_status !== 'active';
    const expiryLine = doc.lifecycle_status === 'active' ? _formatExpiry(doc.expires_at) : '';
    const createdLine = doc.created_at ? new Date(doc.created_at).toLocaleString() : '';

    const previewable = doc.extraction_status === 'ok';
    return `
        <div class="attachment-card${previewable ? ' attachment-card-previewable' : ''}"
             data-document-id="${escapeHtml(doc.document_id)}"
             data-filename="${escapeHtml(doc.filename)}"
             ${previewable ? 'tabindex="0" role="button" aria-label="Preview this file"' : ''}>
            <div class="attachment-card-top">
                <div>
                    <div class="attachment-card-name">${escapeHtml(doc.filename)}</div>
                    <div class="attachment-card-type">${escapeHtml((doc.type || '').replace('.', ''))}</div>
                </div>
                <span class="attachment-status-pill ${statusClass}">${escapeHtml(statusLabel)}</span>
            </div>
            ${doc.status_detail ? `<div class="attachment-card-warning">${escapeHtml(doc.status_detail)}</div>` : ''}
            <div class="attachment-card-meta">
                ${doc.chars != null ? `${doc.chars.toLocaleString()} chars` : ''}
                ${doc.pages ? ` &middot; ${doc.pages} page(s)` : ''}
                ${doc.csv_rows != null ? ` &middot; ${doc.csv_rows} row(s)` : ''}
            </div>
            <div class="attachment-card-meta">
                ${createdLine ? `uploaded ${createdLine}` : ''}${expiryLine ? ` &middot; ${expiryLine}` : ''}
            </div>
            <div class="attachment-card-actions">
                ${needsReupload
                    ? `<button type="button" class="attachment-reupload-btn">Re-upload</button>`
                    : ''}
                <button type="button" class="attachment-delete-btn" data-document-id="${escapeHtml(doc.document_id)}">Delete</button>
            </div>
        </div>`;
}

async function refreshAttachmentsPanel() {
    const body = document.getElementById('attachmentsPanelBody');
    const countEl = document.getElementById('attachmentsPanelCount');
    const badge = document.getElementById('attachmentsPanelBadge');
    if (!body) return;
    let data;
    try {
        data = await fetchAttachments();
    } catch (e) {
        body.innerHTML = `<p class="attachments-panel-empty">Could not load attachments right now.</p>`;
        return;
    }
    const docs = data.attachments || [];
    const activeCount = data.count || 0;
    const maxFiles = data.max_files || 0;

    if (badge) {
        badge.hidden = activeCount === 0;
        badge.textContent = String(activeCount);
    }
    if (countEl) {
        countEl.textContent = maxFiles ? `${activeCount} / ${maxFiles} active` : '';
    }
    if (!docs.length) {
        body.innerHTML = `<p class="attachments-panel-empty">No files attached in this session yet.</p>`;
        return;
    }
    body.innerHTML = docs.map(_renderAttachmentCard).join('');

    body.querySelectorAll('.attachment-delete-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();  // the card itself opens the preview on click
            const documentId = btn.getAttribute('data-document-id');
            btn.disabled = true;
            try {
                await deleteAttachment(documentId);
                showToast('File removed', 'success');
            } catch (e) {
                showToast('Could not delete this file right now.', 'error');
            } finally {
                refreshAttachmentsPanel();
            }
        });
    });
    body.querySelectorAll('.attachment-reupload-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            closeAttachmentsPanel();
            handleFileAttachment();
        });
    });
    body.querySelectorAll('.attachment-card-previewable').forEach(card => {
        const open = () => openAttachmentPreview(
            card.getAttribute('data-document-id'), card.getAttribute('data-filename'));
        card.addEventListener('click', open);
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
        });
    });
}

async function deleteAttachment(documentId) {
    if (!currentSessionId) throw new Error('No active chat session');
    const r = await fetch(
        `${API_BASE_URL}/api/v1/chat/sessions/${currentSessionId}/attachments/${documentId}`,
        { method: 'DELETE', credentials: 'include', headers: _authHeaders() });
    if (!r.ok) {
        let detail = '';
        try { detail = (await r.json()).detail || ''; } catch (_) { /* no body */ }
        throw new Error(detail || `HTTP ${r.status}`);
    }
    return r.json();
}

function openAttachmentsPanel() {
    document.getElementById('attachmentsPanel').hidden = false;
    document.getElementById('attachmentsPanelBackdrop').hidden = false;
    refreshAttachmentsPanel();
}

function closeAttachmentsPanel() {
    const panel = document.getElementById('attachmentsPanel');
    const backdrop = document.getElementById('attachmentsPanelBackdrop');
    if (panel) panel.hidden = true;
    if (backdrop) backdrop.hidden = true;
}

function toggleAttachmentsPanel() {
    const panel = document.getElementById('attachmentsPanel');
    if (panel && !panel.hidden) {
        closeAttachmentsPanel();
    } else {
        openAttachmentsPanel();
    }
}

function initAttachmentsPanel() {
    const openBtn = document.getElementById('attachmentsPanelBtn');
    const closeBtn = document.getElementById('attachmentsPanelCloseBtn');
    const backdrop = document.getElementById('attachmentsPanelBackdrop');
    if (openBtn) openBtn.addEventListener('click', toggleAttachmentsPanel);
    if (closeBtn) closeBtn.addEventListener('click', closeAttachmentsPanel);
    if (backdrop) backdrop.addEventListener('click', closeAttachmentsPanel);
    // The badge count should reflect reality even before the panel is ever
    // opened, and must never be left showing a count from a PRIOR session
    // after a session change / history restore / new chat.
    refreshAttachmentsPanel();
    initAttachmentPreview();
}

async function openAttachmentPreview(documentId, filename) {
    const modal = document.getElementById('attachmentPreviewModal');
    const backdrop = document.getElementById('attachmentPreviewBackdrop');
    const title = document.getElementById('attachmentPreviewTitle');
    const body = document.getElementById('attachmentPreviewBody');
    if (!modal || !backdrop || !title || !body) return;
    title.textContent = filename || 'Preview';
    body.innerHTML = '<p class="attachment-preview-note">Loading…</p>';
    modal.hidden = false;
    backdrop.hidden = false;
    try {
        const data = await fetchAttachmentPreview(documentId);
        body.textContent = (data.text || '').trim()
            || 'No extracted text to show for this file.';
    } catch (e) {
        body.innerHTML = '<p class="attachment-preview-note">Could not load the preview right now.</p>';
    }
}

function closeAttachmentPreview() {
    const modal = document.getElementById('attachmentPreviewModal');
    const backdrop = document.getElementById('attachmentPreviewBackdrop');
    if (modal) modal.hidden = true;
    if (backdrop) backdrop.hidden = true;
}

function initAttachmentPreview() {
    const closeBtn = document.getElementById('attachmentPreviewCloseBtn');
    const backdrop = document.getElementById('attachmentPreviewBackdrop');
    if (closeBtn) closeBtn.addEventListener('click', closeAttachmentPreview);
    if (backdrop) backdrop.addEventListener('click', closeAttachmentPreview);
}
