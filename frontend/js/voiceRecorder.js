/**
 * Voice Recorder Module
 * Cross-browser compatible voice recording using MediaRecorder API
 * Works in Firefox, Chrome, Edge, and Safari
 */

class VoiceRecorder {
    constructor() {
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.stream = null;
        this.isRecording = false;
        this.startTime = null;
        this.timerInterval = null;
        
        // Configuration
        this.maxDuration = 60000; // 60 seconds max
        this.silenceTimeout = null;
        this.silenceDelay = 3000; // Stop after 3 seconds of silence
        
        // Callbacks
        this.onStart = null;
        this.onStop = null;
        this.onError = null;
        this.onTimer = null;
    }
    
    /**
     * Check if browser supports media recording
     */
    isSupported() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
    }
    
    /**
     * Request microphone permission and start recording
     */
    async startRecording() {
        if (!this.isSupported()) {
            throw new Error('Media recording not supported in this browser');
        }
        
        if (this.isRecording) {
            console.warn('Already recording');
            return;
        }
        
        try {
            // Request microphone access
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: 16000 // Optimized for speech recognition
                }
            });
            
            // Determine best supported MIME type
            const mimeType = this.getSupportedMimeType();
            
            // Create MediaRecorder
            this.mediaRecorder = new MediaRecorder(this.stream, {
                mimeType: mimeType
            });
            
            this.audioChunks = [];
            
            // Handle data available
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };
            
            // Handle recording stop
            this.mediaRecorder.onstop = () => {
                this.handleStop();
            };
            
            // Handle errors
            this.mediaRecorder.onerror = (event) => {
                console.error('MediaRecorder error:', event.error);
                if (this.onError) {
                    this.onError(event.error);
                }
                this.cleanup();
            };
            
            // Start recording
            this.mediaRecorder.start(100); // Collect data every 100ms
            this.isRecording = true;
            this.startTime = Date.now();
            
            // Start timer
            this.startTimer();
            
            // Auto-stop after max duration
            setTimeout(() => {
                if (this.isRecording) {
                    this.stopRecording();
                }
            }, this.maxDuration);
            
            if (this.onStart) {
                this.onStart();
            }
            
        } catch (error) {
            console.error('Error starting recording:', error);
            if (this.onError) {
                if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
                    this.onError(new Error('Microphone permission denied'));
                } else if (error.name === 'NotFoundError') {
                    this.onError(new Error('No microphone found'));
                } else {
                    this.onError(error);
                }
            }
            this.cleanup();
            throw error;
        }
    }
    
    /**
     * Stop recording
     */
    stopRecording() {
        if (!this.isRecording) {
            console.warn('Not currently recording');
            return;
        }
        
        this.isRecording = false;
        
        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
            this.mediaRecorder.stop();
        }
        
        this.stopTimer();
    }
    
    /**
     * Handle recording stop
     */
    handleStop() {
        // Create audio blob
        const mimeType = this.mediaRecorder.mimeType;
        const audioBlob = new Blob(this.audioChunks, { type: mimeType });
        
        // Calculate duration
        const duration = Date.now() - this.startTime;
        
        if (this.onStop) {
            this.onStop(audioBlob, duration);
        }
        
        this.cleanup();
    }
    
    /**
     * Start timer
     */
    startTimer() {
        this.timerInterval = setInterval(() => {
            if (this.onTimer) {
                const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
                this.onTimer(elapsed);
            }
        }, 1000);
    }
    
    /**
     * Stop timer
     */
    stopTimer() {
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
            this.timerInterval = null;
        }
    }
    
    /**
     * Clean up resources
     */
    cleanup() {
        this.isRecording = false;
        
        // Stop all tracks
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }
        
        this.stopTimer();
        this.audioChunks = [];
        this.mediaRecorder = null;
    }
    
    /**
     * Get supported MIME type for recording
     */
    getSupportedMimeType() {
        const types = [
            'audio/webm;codecs=opus',
            'audio/webm',
            'audio/ogg;codecs=opus',
            'audio/ogg',
            'audio/wav'
        ];
        
        for (const type of types) {
            if (MediaRecorder.isTypeSupported(type)) {
                return type;
            }
        }
        
        // Fallback to default
        return '';
    }
    
    /**
     * Get file extension for MIME type
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
    module.exports = VoiceRecorder;
}
