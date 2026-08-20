/**
 * Chat Storage Module
 * Persists chat history and session ID in localStorage so they survive page
 * refreshes. Data is only wiped on explicit logout or "New Chat".
 *
 * localStorage keys are namespaced per officer so multiple accounts on the
 * same browser don't share history.
 */

const STORAGE_KEY_CHAT_HISTORY = 'sis_chat_history';
const STORAGE_KEY_SESSION_ID   = 'sis_current_session_id';
const MAX_HISTORY_MESSAGES     = 100; // Keep last 100 messages

/**
 * Return the officer-scoped storage key.
 *
 * sessionStorage is tab-scoped and may be empty on a hard refresh before
 * auth re-populates it.  We persist the last-known employee_id in
 * localStorage ('sis_last_officer_id') so the key stays stable across
 * refreshes and the chat history is never "lost" just because the page
 * reloaded before auth finished.
 *
 * Falls back to the bare base key only when no officer id is known at all.
 */
function _scopedKey(base) {
    try {
        // Primary: sessionStorage (set by auth after login)
        const raw = sessionStorage.getItem('officer_data');
        if (raw) {
            const d = JSON.parse(raw);
            const id = d.employee_id || d.officer_id || '';
            if (id) {
                // Keep localStorage in sync so refreshes can find the same key
                localStorage.setItem('sis_last_officer_id', id);
                return `${base}_${id}`;
            }
        }
    } catch (_) { /* ignore */ }

    try {
        // Fallback: last known id persisted in localStorage
        const id = localStorage.getItem('sis_last_officer_id');
        if (id) return `${base}_${id}`;
    } catch (_) { /* ignore */ }

    return base;
}

/**
 * Save chat history to localStorage
 */
function saveChatHistoryToStorage(history) {
    try {
        const trimmedHistory = history.slice(-MAX_HISTORY_MESSAGES);
        localStorage.setItem(_scopedKey(STORAGE_KEY_CHAT_HISTORY), JSON.stringify(trimmedHistory));
        console.log(`💾 Saved ${trimmedHistory.length} messages to localStorage`);
    } catch (error) {
        console.error('Error saving chat history:', error);
    }
}

/**
 * Load chat history from localStorage
 */
function loadChatHistoryFromStorage() {
    try {
        const stored = localStorage.getItem(_scopedKey(STORAGE_KEY_CHAT_HISTORY));
        if (stored) {
            const history = JSON.parse(stored);
            console.log(`📂 Loaded ${history.length} messages from localStorage`);
            return history;
        }
    } catch (error) {
        console.error('Error loading chat history:', error);
    }
    return [];
}

/**
 * Add a single message to history and persist it
 */
function addMessageToHistory(role, content, language = 'auto', tableData = null) {
    const message = {
        role,
        content,
        language,
        tableData,
        timestamp: new Date().toISOString()
    };

    const history = loadChatHistoryFromStorage();
    history.push(message);
    saveChatHistoryToStorage(history);

    return message;
}

/**
 * Clear chat history from localStorage (called on logout or new chat)
 */
function clearChatHistory() {
    try {
        localStorage.removeItem(_scopedKey(STORAGE_KEY_CHAT_HISTORY));
        localStorage.removeItem(_scopedKey(STORAGE_KEY_SESSION_ID));
        // On a full logout also remove the persisted officer id so a
        // different user on the same browser starts with a clean slate.
        const isLoggingOut = !sessionStorage.getItem('officer_data');
        if (isLoggingOut) {
            localStorage.removeItem('sis_last_officer_id');
        }
        console.log('🗑️ Cleared chat history from localStorage');
    } catch (error) {
        console.error('Error clearing chat history:', error);
    }
}

/**
 * Save current session ID to localStorage
 */
function saveSessionId(sessionId) {
    try {
        localStorage.setItem(_scopedKey(STORAGE_KEY_SESSION_ID), sessionId);
        console.log('💾 Saved session ID to localStorage:', sessionId);
    } catch (error) {
        console.error('Error saving session ID:', error);
    }
}

/**
 * Load current session ID from localStorage
 */
function loadSessionId() {
    try {
        const sessionId = localStorage.getItem(_scopedKey(STORAGE_KEY_SESSION_ID));
        if (sessionId) {
            console.log('📂 Loaded session ID from localStorage:', sessionId);
            return sessionId;
        }
    } catch (error) {
        console.error('Error loading session ID:', error);
    }
    return null;
}

/**
 * Get chat history formatted for API context (last N messages)
 */
function getChatHistoryForAPI(limit = 10) {
    const history = loadChatHistoryFromStorage();
    // Only role/content/language/timestamp are meaningful to the backend —
    // drop the (potentially large) rendered table payload.
    return history.slice(-limit).map(({ role, content, language, timestamp }) => ({
        role, content, language, timestamp
    }));
}

// Export API
window.chatStorage = {
    save:          saveChatHistoryToStorage,
    load:          loadChatHistoryFromStorage,
    addMessage:    addMessageToHistory,
    clear:         clearChatHistory,
    saveSessionId: saveSessionId,
    loadSessionId: loadSessionId,
    getForAPI:     getChatHistoryForAPI
};
