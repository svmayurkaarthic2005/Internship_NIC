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
        renderMessage(msg.role, msg.content, msg.timestamp, msg.language || 'auto', true);
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
    // Send button
    if (sendBtn) {
        sendBtn.addEventListener('click', sendMessage);
    }
    
    // Message input
    if (messageInput) {
        messageInput.addEventListener('input', handleInputChange);
        messageInput.addEventListener('keydown', handleKeyDown);
    }
    
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
    
    // Mic button
    const micBtn = document.querySelector('.btn-icon-action-new[aria-label="Voice input"]');
    if (micBtn) {
        micBtn.addEventListener('click', handleVoiceInput);
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
    sendBtn.disabled = !text || isTyping;
    
    // Detect language
    if (text) {
        const language = detectLanguage(text);
        updateLanguageIndicator(language);
        
        // Auto-resize textarea
        messageInput.style.height = 'auto';
        messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
    }
}

// Safe wrapper — languageIndicator/languageText don't exist in the current chatbot.html
const _languageText = document.getElementById('languageText');
const _languageIndicator = document.getElementById('languageIndicator');

/**
 * Handle keyboard shortcuts
 */
function handleKeyDown(event) {
    // Enter without Shift sends message
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        if (!sendBtn.disabled) {
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
    sendBtn.disabled = true;
    isTyping = true;
    
    // Show typing indicator
    showTypingIndicator();
    
    // Abort controller so we can cancel if the LLM hangs
    const controller = new AbortController();
    // 90 second hard timeout — LLMs can be slow but shouldn't run forever
    const timeoutId = setTimeout(() => {
        controller.abort();
        console.warn('sendMessage: stream timed out after 90s');
    }, 90000);
    
    try {
        // Get chat history from sessionStorage for context (last 10 messages)
        const chatHistory = window.chatStorage ? window.chatStorage.getForAPI(10) : [];
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
        let aiResponse = '';
        let capturedTableData = null;  // capture table_data from SSE

        // Create an empty message div to stream into
        const messageDiv = document.createElement('div');
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
        
        const contentDiv = messageDiv.querySelector('#streaming-content');
        
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
                window.chatStorage.addMessage('assistant', aiResponse, 'auto');
            }
            messageHistory = window.chatStorage ? window.chatStorage.load() : [];
        }
        
    } catch (error) {
        removeTypingIndicator();
        console.error('=== SEND MESSAGE ERROR ===');
        console.error('Error type:', error.name);
        console.error('Error message:', error.message);
        console.error('Full error:', error);
        
        if (error.name === 'AbortError') {
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
        handleInputChange();
        console.log('=== SEND MESSAGE COMPLETE ===');
    }
}

/**
 * Handle Application Chip Click
 */
window.handleAppChipClick = function(element) {
    const appNumber = element.getAttribute('data-app');
    if (appNumber) {
        const query = `What is the status of ${appNumber}?`;
        if (messageInput) {
            messageInput.value = query;
            handleInputChange();
            sendMessage();
        }
    }
}

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
 * Voice Recording - Cross-browser compatible using MediaRecorder API
 */
let voiceRecorder = null;
let speechAPI = null;
let isRecordingVoice = false;

/**
 * Initialize voice recording system
 */
function initializeVoiceRecording() {
    voiceRecorder = new VoiceRecorder();
    speechAPI = new SpeechAPI();
    
    // Setup callbacks
    voiceRecorder.onStart = handleRecordingStart;
    voiceRecorder.onStop = handleRecordingStop;
    voiceRecorder.onError = handleRecordingError;
    voiceRecorder.onTimer = handleRecordingTimer;
}

/**
 * Handle voice input button click
 */
function handleVoiceInput() {
    if (!voiceRecorder) {
        initializeVoiceRecording();
    }
    
    // Check if supported
    if (!voiceRecorder.isSupported()) {
        showToast('Voice input not supported in this browser', 'error');
        return;
    }
    
    // Toggle recording
    if (isRecordingVoice) {
        stopVoiceRecording();
    } else {
        startVoiceRecording();
    }
}

/**
 * Start voice recording
 */
async function startVoiceRecording() {
    try {
        await voiceRecorder.startRecording();
        isRecordingVoice = true;
    } catch (error) {
        console.error('Failed to start recording:', error);
        isRecordingVoice = false;
    }
}

/**
 * Stop voice recording
 */
function stopVoiceRecording() {
    if (voiceRecorder && isRecordingVoice) {
        voiceRecorder.stopRecording();
    }
}

/**
 * Handle recording start
 */
function handleRecordingStart() {
    const micBtn = document.querySelector('.btn-icon-action-new[aria-label="Voice input"]');
    
    if (micBtn) {
        // Change button to recording state
        micBtn.style.background = 'var(--danger)';
        micBtn.style.color = 'white';
        micBtn.classList.add('recording');
        
        // Add pulse animation
        micBtn.style.animation = 'pulse 1.5s ease-in-out infinite';
        
        // Change icon or add recording indicator
        const icon = micBtn.querySelector('.mic-icon-new');
        if (icon) {
            icon.style.animation = 'pulse 1s ease-in-out infinite';
        }
        
        // Add timer display
        const timerEl = document.createElement('span');
        timerEl.id = 'recording-timer';
        timerEl.style.cssText = `
            position: absolute;
            top: -24px;
            left: 50%;
            transform: translateX(-50%);
            background: var(--danger);
            color: white;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
        `;
        timerEl.textContent = '00:00';
        micBtn.style.position = 'relative';
        micBtn.appendChild(timerEl);
    }
    
    showToast('🎤 Recording... Click again to stop', 'info');
}

/**
 * Handle recording stop
 */
async function handleRecordingStop(audioBlob, duration) {
    isRecordingVoice = false;
    
    const micBtn = document.querySelector('.btn-icon-action-new[aria-label="Voice input"]');
    
    // Reset button state
    if (micBtn) {
        micBtn.style.background = '';
        micBtn.style.color = '';
        micBtn.style.animation = '';
        micBtn.classList.remove('recording');
        
        const icon = micBtn.querySelector('.mic-icon-new');
        if (icon) {
            icon.style.animation = '';
        }
        
        const timer = document.getElementById('recording-timer');
        if (timer) {
            timer.remove();
        }
    }
    
    // Check if audio is too short
    if (duration < 500) {
        showToast('Recording too short. Please try again.', 'warning');
        return;
    }
    
    // Log audio details for debugging
    console.log('📊 Audio Recording Details:');
    console.log('  - Size:', audioBlob.size, 'bytes');
    console.log('  - Type:', audioBlob.type);
    console.log('  - Duration:', duration, 'ms');
    
    // Warn if audio is suspiciously small
    if (audioBlob.size < 1000) {
        console.warn('⚠️ Audio file is very small, may be empty or corrupted');
    }
    
    // Get access token - with fallback to sessionStorage
    let accessToken = null;
    if (officerData && officerData.access_token) {
        accessToken = officerData.access_token;
        console.log('✓ Using access token from officerData');
    } else {
        // Fallback: try to get from sessionStorage
        const storedData = sessionStorage.getItem('officer_data');
        if (storedData) {
            try {
                const data = JSON.parse(storedData);
                accessToken = data.access_token;
                console.log('✓ Using access token from sessionStorage');
            } catch (e) {
                console.error('Failed to parse officer_data from sessionStorage:', e);
            }
        }
    }
    
    if (!accessToken) {
        console.error('❌ No access token available!');
        showToast('Authentication error. Please refresh the page and try again.', 'error');
        return;
    }
    
    // Log token info (first 20 chars only for security)
    console.log('🔐 Token preview:', accessToken.substring(0, 20) + '...');
    
    // Show transcribing state
    showToast('⏳ Transcribing audio...', 'info');
    
    try {
        // Upload and transcribe
        const result = await speechAPI.transcribe(audioBlob, accessToken);
        
        if (result.text && result.text.trim()) {
            // Fill input with transcribed text
            messageInput.value = result.text;
            handleInputChange();
            messageInput.focus();
            
            showToast('✓ Voice input captured: ' + result.text.substring(0, 50), 'success');
        } else {
            showToast('No speech detected. Please try again.', 'warning');
        }
        
    } catch (error) {
        console.error('❌ Transcription error:', error);
        console.error('Error details:', error.message);
        
        let errorMessage = 'Speech recognition failed. ';
        
        if (error.message.includes('Could not validate credentials')) {
            errorMessage = '🔐 Authentication failed. Please refresh the page and log in again.';
        } else if (error.message.includes('401')) {
            errorMessage = '🔐 Session expired. Please refresh the page and log in again.';
        } else if (error.message.includes('permission')) {
            errorMessage = '🎤 Microphone permission denied.';
        } else if (error.message.includes('not found')) {
            errorMessage = '🎤 No microphone found.';
        } else if (error.message.includes('network') || error.message.includes('fetch')) {
            errorMessage = '🌐 Network error. Please check your connection.';
        } else if (error.message.includes('Unsupported audio format')) {
            errorMessage = '⚠️ ' + error.message;
        } else if (error.message.includes('FFmpeg') || error.message.includes('ffmpeg')) {
            errorMessage = '⚠️ Server audio processing error. Contact administrator.';
        } else if (error.message.includes('No speech detected')) {
            errorMessage = '🔇 No speech detected. Please speak more clearly and try again.';
        } else if (error.message.includes('Speech model')) {
            errorMessage = '⚠️ Speech model error. Please try again or contact support.';
        } else if (error.message) {
            errorMessage = '⚠️ ' + error.message;
        } else {
            errorMessage += 'Please try again.';
        }
        
        showToast(errorMessage, 'error');
    }
}

/**
 * Handle recording error
 */
function handleRecordingError(error) {
    isRecordingVoice = false;
    
    const micBtn = document.querySelector('.btn-icon-action-new[aria-label="Voice input"]');
    
    // Reset button state
    if (micBtn) {
        micBtn.style.background = '';
        micBtn.style.color = '';
        micBtn.style.animation = '';
        micBtn.classList.remove('recording');
        
        const timer = document.getElementById('recording-timer');
        if (timer) {
            timer.remove();
        }
    }
    
    let errorMessage = 'Voice input error: ';
    
    if (error.message.includes('permission denied')) {
        errorMessage = 'Microphone permission denied. Please enable microphone access in browser settings.';
    } else if (error.message.includes('not found')) {
        errorMessage = 'No microphone found. Please connect a microphone and try again.';
    } else {
        errorMessage += error.message;
    }
    
    showToast(errorMessage, 'error');
}

/**
 * Handle recording timer update
 */
function handleRecordingTimer(seconds) {
    const timer = document.getElementById('recording-timer');
    if (timer) {
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        timer.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }
}

/**
 * Handle file attachment button
 */
function handleFileAttachment() {
    // Create hidden file input
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = 'image/*,.pdf,.doc,.docx,.txt';
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
        
        // Validate file type
        const allowedTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf', 
                             'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                             'text/plain'];
        
        if (!allowedTypes.includes(file.type)) {
            showToast('Unsupported file type. Please upload images, PDF, DOC, or TXT files.', 'error');
            return;
        }
        
        // Analyze file
        showToast('Analyzing file...', 'info');
        const fileAnalysis = await analyzeFile(file);
        
        // Show file preview with analysis
        showFilePreview(file, fileAnalysis);
        
        // For text files: auto-fill the input with file content so the bot can answer.
        // For PDF/Word: show a clear notice — document content extraction is not yet
        // supported, so we must NOT send a misleading generic question that causes
        // the bot to return random / unrelated answers.
        if (fileAnalysis.category === 'text' || fileAnalysis.category === 'image') {
            if (fileAnalysis.canAnalyze) {
                const question = generateFileQuestion(file, fileAnalysis);
                if (question) {
                    setTimeout(() => {
                        messageInput.value = question;
                        handleInputChange();
                        messageInput.focus();
                    }, 500);
                }
            }
        } else if (fileAnalysis.category === 'pdf' || fileAnalysis.category === 'document') {
            // Show an assistant-style notice directly in the chat
            const noticeDiv = document.createElement('div');
            noticeDiv.className = 'message message-assistant';
            const time = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
            noticeDiv.innerHTML = `
                <div class="message-avatar">
                    <i data-lucide="bot" class="avatar-icon"></i>
                </div>
                <div class="message-content-wrapper">
                    <div class="message-content">
                        <strong>📄 Document uploaded: ${escapeHtml(file.name)}</strong><br><br>
                        I can see you've uploaded a ${fileAnalysis.category === 'pdf' ? 'PDF' : 'Word'} document.
                        Currently, I can only read <strong>plain text (.txt)</strong> file contents directly.<br><br>
                        To get answers from this document, please:<br>
                        • Copy and paste the relevant text into the chat, or<br>
                        • Ask me a specific SIS question (surveys, applications, field visits, etc.)
                    </div>
                    <div class="message-footer">
                        <span class="message-time">${time}</span>
                    </div>
                </div>
            `;
            chatMessages.appendChild(noticeDiv);
            if (typeof lucide !== 'undefined') lucide.createIcons();
            scrollToBottom();
        }
    };
    
    // Trigger file selection
    document.body.appendChild(fileInput);
    fileInput.click();
    
    // Clean up
    setTimeout(() => {
        document.body.removeChild(fileInput);
    }, 1000);
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
    
    // Image analysis
    if (file.type.startsWith('image/')) {
        analysis.canAnalyze = true;
        analysis.category = 'image';
        
        try {
            // Read image data
            const imageData = await readFileAsDataURL(file);
            analysis.preview = imageData;
            
            // Get image dimensions
            const dimensions = await getImageDimensions(imageData);
            analysis.metadata = {
                width: dimensions.width,
                height: dimensions.height,
                aspectRatio: (dimensions.width / dimensions.height).toFixed(2),
                megapixels: ((dimensions.width * dimensions.height) / 1000000).toFixed(2)
            };
        } catch (error) {
            console.error('Error analyzing image:', error);
        }
    }
    
    // Text file analysis
    else if (file.type === 'text/plain') {
        analysis.canAnalyze = true;
        analysis.category = 'text';
        
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
    
    // PDF analysis
    else if (file.type === 'application/pdf') {
        analysis.canAnalyze = true;
        analysis.category = 'pdf';
        analysis.metadata = {
            info: 'PDF document - Content extraction requires backend processing'
        };
    }
    
    // Document analysis
    else if (file.type.includes('word') || file.type.includes('document')) {
        analysis.canAnalyze = true;
        analysis.category = 'document';
        analysis.metadata = {
            info: 'Word document - Content extraction requires backend processing'
        };
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
 * Read file as Data URL
 */
function readFileAsDataURL(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => resolve(e.target.result);
        reader.onerror = (e) => reject(e);
        reader.readAsDataURL(file);
    });
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
 * Get image dimensions
 */
function getImageDimensions(dataUrl) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => {
            resolve({ width: img.width, height: img.height });
        };
        img.onerror = reject;
        img.src = dataUrl;
    });
}

/**
 * Generate question about uploaded file
 */
function generateFileQuestion(file, analysis) {
    if (analysis.category === 'image') {
        return `Can you help me analyze this image: ${file.name}? What can you tell me about it?`;
    } else if (analysis.category === 'text') {
        // For text files we append the content directly so the bot can actually answer
        const preview = analysis.metadata.preview || '';
        if (preview) {
            return `Here is the content of the file "${file.name}":\n\n${preview}\n\nPlease answer based on this content.`;
        }
        return `I've uploaded a text file "${file.name}". Can you help me understand or summarize its content?`;
    }
    // For PDF / Word: do NOT generate a question — showFilePreview handles the UI
    // and we will show an explicit notice instead of routing to the chatbot.
    return '';
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
        
        if (analysis.category === 'image') {
            previewContent += `
                <div>📐 ${analysis.metadata.width} × ${analysis.metadata.height} px</div>
                <div>🖼️ ${analysis.metadata.megapixels} MP</div>
            `;
        } else if (analysis.category === 'text') {
            previewContent += `
                <div>� ${analysis.metadata.lines} lines</div>
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
    
    // Add image preview if available
    if (analysis.category === 'image' && analysis.preview) {
        const contentDiv = messageDiv.querySelector('.file-attachment');
        const img = document.createElement('img');
        img.src = analysis.preview;
        img.className = 'file-image-preview';
        img.style.cssText = `
            max-width: 100%;
            max-height: 200px;
            border-radius: var(--radius-md);
            margin-top: var(--spacing-md);
            display: block;
        `;
        contentDiv.appendChild(img);
    }
    
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
    
    try {
        const response = await fetch(`${API_BASE_URL}/api/v1/chat/upload`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${officerData.access_token}`
            },
            credentials: 'include',
            body: formData
        });
        
        if (!response.ok) {
            throw new Error('Upload failed');
        }
        
        const data = await response.json();
        showToast('File uploaded successfully', 'success');
        return data;
        
    } catch (error) {
        console.error('File upload error:', error);
        showToast('Failed to upload file', 'error');
        throw error;
    }
}
