/**
 * Speech API Client
 * Handles communication with backend speech-to-text endpoint
 */

class SpeechAPI {
    constructor(baseURL = '') {
        this.baseURL = baseURL || window.API_BASE_URL || 'http://localhost:8000';
        this.endpoint = `${this.baseURL}/api/v1/speech/transcribe`;
    }
    
    /**
     * Upload audio and get transcription
     * @param {Blob} audioBlob - Audio blob from MediaRecorder
     * @param {string} accessToken - JWT access token
     * @param {string} language - Optional language code
     * @returns {Promise<{text: string, language: string}>}
     */
    async transcribe(audioBlob, accessToken, language = null) {
        try {
            // Validate inputs
            if (!audioBlob || !(audioBlob instanceof Blob)) {
                throw new Error('Invalid audio blob');
            }
            
            if (!accessToken || typeof accessToken !== 'string') {
                throw new Error('Invalid or missing access token');
            }
            
            console.log('🎤 Speech API Request:');
            console.log('  - Endpoint:', this.endpoint);
            console.log('  - Audio size:', audioBlob.size, 'bytes');
            console.log('  - Audio type:', audioBlob.type);
            console.log('  - Token available:', !!accessToken);
            
            // Create FormData
            const formData = new FormData();
            
            // Determine file extension based on blob type
            const extension = this.getFileExtension(audioBlob.type);
            const filename = `audio_${Date.now()}.${extension}`;
            
            formData.append('audio', audioBlob, filename);
            
            if (language) {
                formData.append('language', language);
            }
            
            // Make request
            const response = await fetch(this.endpoint, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${accessToken}`
                },
                credentials: 'include',
                body: formData
            });
            
            console.log('📡 Speech API Response:', response.status, response.statusText);
            
            if (!response.ok) {
                let errorDetail = `HTTP ${response.status}: ${response.statusText}`;
                
                try {
                    const errorData = await response.json();
                    console.error('Speech API error data:', errorData);
                    errorDetail = errorData.detail || errorDetail;
                } catch (parseError) {
                    console.warn('Could not parse error response:', parseError);
                }
                
                throw new Error(errorDetail);
            }
            
            const data = await response.json();
            console.log('✓ Transcription successful:', data.text ? data.text.substring(0, 50) + '...' : '(empty)');
            
            return {
                text: data.text || '',
                language: data.language || 'unknown',
                languageProbability: data.language_probability || 0
            };
            
        } catch (error) {
            console.error('❌ Speech API error:', error);
            throw error;
        }
    }
    
    /**
     * Check if speech service is available
     * @returns {Promise<{status: string, model: string}>}
     */
    async checkHealth() {
        try {
            const response = await fetch(`${this.baseURL}/api/v1/speech/health`);
            const data = await response.json();
            return data;
        } catch (error) {
            console.error('Speech health check failed:', error);
            return { status: 'unavailable' };
        }
    }
    
    /**
     * Get file extension from MIME type
     */
    getFileExtension(mimeType) {
        if (mimeType.includes('webm')) return 'webm';
        if (mimeType.includes('ogg')) return 'ogg';
        if (mimeType.includes('wav')) return 'wav';
        return 'webm'; // Default
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = SpeechAPI;
}
