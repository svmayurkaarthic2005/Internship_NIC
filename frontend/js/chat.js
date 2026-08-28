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
const LONG_CONVERSATION_EXCHANGES = 10;   // user turns before we recommend a new chat

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
        
        const data = await response.json();
        currentSessionId = data.data.session_id;
        console.log('Session created:', currentSessionId);
        
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
    const welcomeMessage = `வணக்கம்! I'm your SIS AI Assistant. I can help you with:

• Survey numbers and sub-divisions
• ISD, NISD, and Merge applications
• Field visit scheduling
• Application status tracking
• Workflow questions
• Workload management

How can I assist you today?`;
    
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
        sendBtn.disabled = !text;
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
        // Restore normal enabled/disabled logic based on current input text
        sendBtn.disabled = !messageInput.value.trim();
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
    

    
    if (isTyping) {
        console.warn('❌ Cannot send: Already typing');
        showToast('Please wait for the current response', 'warning');
        return;
    }
    
    if (!currentSessionId) {
        console.error('❌ Cannot send: No session ID');
        showToast('Session error. Please refresh the page.', 'error');
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
    const chatHistory = window.chatStorage ? window.chatStorage.getForAPI(10) : [];

    // Save user message to localStorage before rendering
    const userTimestamp = new Date().toISOString();
    if (window.chatStorage) {
        window.chatStorage.addMessage('user', text, detectLanguage(text));
    }
    
    // Render user message
    renderMessage('user', text, userTimestamp, detectLanguage(text));
    
    // Clear input
    messageInput.value = '';
    messageInput.style.height = 'auto';
    isTyping = true;
    userStoppedResponse = false;
    setSendButtonState(true); // send button becomes the stop button
    
    // Show typing indicator
    showTypingIndicator();
    
    // Abort controller so we can cancel if the LLM hangs, or the user clicks Stop
    const controller = new AbortController();
    activeRequestController = controller;
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
        
        while (true) {
            const { value, done } = await reader.read();
            
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
                    if (parsed.content) {
                        aiResponse += parsed.content;
                        contentDiv.innerHTML = formatBotMessage(aiResponse.trimStart());
                    }
                } catch (e) {
                    console.warn('Could not parse final SSE JSON:', e);
                }
            }
        }
        
        // Check if we got any response
        if (!aiResponse && messageDiv && messageDiv.parentNode) {
            console.error('No content received from stream!');
            contentDiv.innerHTML = '<span style="color: orange;">⚠️ No response received. Please try again.</span>';
        }

        contentDiv.removeAttribute('id');

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
        if (aiResponse) {
            if (window.chatStorage) {
                // Persist the table payload too, so tables survive a page refresh
                window.chatStorage.addMessage('assistant', aiResponse, 'auto', capturedTableData);
            }
            messageHistory = window.chatStorage ? window.chatStorage.load() : [];
            maybeShowLongConversationNotice();
        }

    } catch (error) {
        removeTypingIndicator();
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
        isTyping = false;
        activeRequestController = null;
        userStoppedResponse = false;
        setSendButtonState(false); // restore the normal send icon/behaviour
        handleInputChange();
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
function formatBotMessage(text) {
    // Detect HTML: any opening tag at all (e.g. <table, <p, <ul, <strong …)
    const hasHTML = /<[a-zA-Z][^>]*>/i.test(text);
    if (hasHTML) {
        // Trusted HTML from the backend — inject as-is.
        return text.trim();
    }
    
    // ── Plain text → light markdown conversion ───────────────────────
    let formatted = escapeHtml(text);
    
    // Bold: **text**
    formatted = formatted.replace(/\*\*(.*?)\*\*/gs, '<strong>$1</strong>');
    
    // Inline code: `code`
    formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');
    
    // Collapse 3+ blank lines → single blank line, then convert newlines → <br>
    formatted = formatted.replace(/\n{3,}/g, '\n\n');
    formatted = formatted.replace(/\n/g, '<br>');
    
    // Bullet lists: lines starting with • or -
    formatted = formatted.replace(/^[•\-]\s+(.+)$/gm, '<li>$1</li>');
    if (/<li>/.test(formatted)) {
        formatted = formatted.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
    }
    
    // Numbered lists: lines starting with "1. "
    if (/^\d+\.\s/.test(text)) {
        formatted = formatted.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');
        formatted = formatted.replace(/(<li>[\s\S]*?<\/li>)/g, '<ol>$1</ol>');
    }
    
    // Application number chips: ISD/…/…/…
    formatted = formatted.replace(
        /\b((?:ISD|NISD|MERGE)\/\w+\/\d+\/\d+)\b/gi,
        '<span class="suggestion-chip" data-app="$1" onclick="handleAppChipClick(this)" ' +
        'style="cursor:pointer;display:inline-block;margin:2px;">$1</span>'
    );
    
    return formatted;
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
async function handleNewChat() {
    chatMessages.innerHTML = '';
    messageHistory = [];
    longConversationNoticeShown = false;

    if (window.chatStorage) {
        window.chatStorage.clear();
    }
    
    await createNewSession();
    
    if (window.chatStorage && currentSessionId) {
        window.chatStorage.saveSessionId(currentSessionId);
    }
    
    renderWelcomeMessage();
    
    if (quickSuggestions) {
        quickSuggestions.style.display = 'block';
    }
    
    showToast('New chat started', 'success');
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
function handleFileAttachment() {
    // Create hidden file input
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.txt,.csv,.pdf,.doc,.docx';
    fileInput.multiple = false;
    fileInput.style.display = 'none';

    fileInput.onchange = async (event) => {
        const file = event.target.files[0];
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
                } else {
                    const msg = (result && result.message) ||
                        `"${file.name}" attached. Ask your question about it now.`;
                    appendAssistantNotice(`📎 <strong>${escapeHtml(file.name)}</strong> attached.<br>${escapeHtml(msg)}`);
                    setTimeout(() => {
                        messageInput.value = `About ${file.name}: `;
                        handleInputChange();
                        messageInput.focus();
                    }, 300);
                }
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
    };

    document.body.appendChild(fileInput);
    fileInput.click();
    setTimeout(() => { document.body.removeChild(fileInput); }, 1000);
}

/**
 * After enough back-and-forth, tell the officer to start a new chat — the API
 * only keeps the last few messages in context, so a very long thread starts to
 * "forget" its own beginning. Shown once per session; sending still works.
 */
function maybeShowLongConversationNotice() {
    if (longConversationNoticeShown) return;
    const userTurns = (messageHistory || []).filter(m => m.role === 'user').length;
    if (userTurns < LONG_CONVERSATION_EXCHANGES) return;

    longConversationNoticeShown = true;
    appendAssistantNotice(
        `💡 <strong>This conversation is getting long (${userTurns} messages).</strong><br>` +
        `I only keep the last few messages in view, so older context may be dropped. ` +
        `For best results, click <strong>New Chat</strong> to start a fresh conversation. ` +
        `You can keep going here if you prefer.`);
}

/**
 * Append a short assistant-style notice bubble (not persisted to history).
 */
function appendAssistantNotice(html) {
    const div = document.createElement('div');
    div.className = 'message message-assistant';
    const time = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    div.innerHTML = `
        <div class="message-avatar"><i data-lucide="bot" class="avatar-icon"></i></div>
        <div class="message-content-wrapper">
            <div class="message-content">${html}</div>
            <div class="message-footer"><span class="message-time">${time}</span></div>
        </div>`;
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
    return body;
}
