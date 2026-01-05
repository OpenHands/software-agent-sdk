class OpenHandsWebChat {
    constructor() {
        // In Docker setup, API calls go through nginx proxy
        this.apiBaseUrl = window.location.origin + '/api';
        this.wsBaseUrl = window.location.protocol === 'https:' 
            ? `wss://${window.location.host}`
            : `ws://${window.location.host}`;
        
        this.currentConversationId = null;
        this.websocket = null;
        this.conversations = new Map();
        this.isAgentRunning = false;
        
        this.initializeElements();
        this.attachEventListeners();
        this.loadConversations();
        
        // Auto-resize textarea
        this.setupTextareaAutoResize();
    }

    initializeElements() {
        // Main elements
        this.conversationsContainer = document.getElementById('conversations-container');
        this.chatMessages = document.getElementById('chat-messages');
        this.messageInput = document.getElementById('message-input');
        this.sendBtn = document.getElementById('send-btn');
        this.connectionStatus = document.getElementById('connection-status');
        this.typingIndicator = document.getElementById('typing-indicator');
        
        // Header elements
        this.conversationTitle = document.getElementById('current-conversation-title');
        this.conversationStatus = document.getElementById('conversation-status');
        this.pauseBtn = document.getElementById('pause-btn');
        this.resumeBtn = document.getElementById('resume-btn');
        this.deleteBtn = document.getElementById('delete-conversation-btn');
        
        // Modal elements
        this.newConversationModal = document.getElementById('new-conversation-modal');
        this.newConversationForm = document.getElementById('new-conversation-form');
        this.initialMessageInput = document.getElementById('initial-message');
        this.jsonParametersInput = document.getElementById('json-parameters');
        this.jsonValidationError = document.getElementById('json-validation-error');
        this.resetJsonBtn = document.getElementById('reset-json-btn');
        this.showJsonHelpBtn = document.getElementById('show-json-help');
        
        // Loading overlay
        this.loadingOverlay = document.getElementById('loading-overlay');
    }

    attachEventListeners() {
        // Sidebar buttons
        document.getElementById('new-conversation-btn').addEventListener('click', () => {
            this.showNewConversationModal();
        });
        
        document.getElementById('refresh-conversations').addEventListener('click', () => {
            this.loadConversations();
        });

        // Chat controls
        this.pauseBtn.addEventListener('click', () => this.pauseConversation());
        this.resumeBtn.addEventListener('click', () => this.resumeConversation());
        this.deleteBtn.addEventListener('click', () => this.deleteConversation());

        // Message input
        this.messageInput.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') {
                e.preventDefault();
                this.sendMessage();
            }
        });
        
        this.sendBtn.addEventListener('click', () => this.sendMessage());

        // Modal events
        document.getElementById('create-conversation').addEventListener('click', () => {
            this.createNewConversation();
        });
        
        document.getElementById('cancel-new-conversation').addEventListener('click', () => {
            this.hideNewConversationModal();
        });
        
        document.querySelector('.modal-close').addEventListener('click', () => {
            this.hideNewConversationModal();
        });
        
        // Close modal on outside click
        this.newConversationModal.addEventListener('click', (e) => {
            if (e.target === this.newConversationModal) {
                this.hideNewConversationModal();
            }
        });

        // JSON parameters controls
        this.resetJsonBtn.addEventListener('click', () => {
            this.resetJsonParameters();
        });

        this.showJsonHelpBtn.addEventListener('click', (e) => {
            e.preventDefault();
            this.showJsonExample();
        });

        // JSON validation on input
        this.jsonParametersInput.addEventListener('input', () => {
            this.validateJsonParameters();
        });
    }

    setupTextareaAutoResize() {
        this.messageInput.addEventListener('input', () => {
            this.messageInput.style.height = 'auto';
            this.messageInput.style.height = Math.min(this.messageInput.scrollHeight, 120) + 'px';
        });
    }

    showLoading() {
        this.loadingOverlay.style.display = 'flex';
    }

    hideLoading() {
        this.loadingOverlay.style.display = 'none';
    }

    updateConnectionStatus(status) {
        this.connectionStatus.className = `connection-status ${status}`;
        const icon = this.connectionStatus.querySelector('i');
        const text = this.connectionStatus.childNodes[1];
        
        switch (status) {
            case 'connected':
                icon.className = 'fas fa-circle';
                text.textContent = ' Connected';
                break;
            case 'connecting':
                icon.className = 'fas fa-circle-notch fa-spin';
                text.textContent = ' Connecting...';
                break;
            case 'disconnected':
            default:
                icon.className = 'fas fa-circle';
                text.textContent = ' Disconnected';
                break;
        }
    }

    async apiRequest(endpoint, options = {}) {
        const url = `${this.apiBaseUrl}${endpoint}`;
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
            },
        };
        
        const response = await fetch(url, { ...defaultOptions, ...options });
        
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`API request failed: ${response.status} ${errorText}`);
        }
        
        return response.json();
    }

    async loadConversations() {
        try {
            const data = await this.apiRequest('/conversations/search?limit=50');
            this.conversations.clear();
            
            this.conversationsContainer.innerHTML = '';
            
            if (data.items && data.items.length > 0) {
                data.items.forEach(conversation => {
                    this.conversations.set(conversation.id, conversation);
                    this.addConversationToSidebar(conversation);
                });
            } else {
                this.conversationsContainer.innerHTML = 
                    '<div style="padding: 20px; text-align: center; color: #bdc3c7;">No conversations yet</div>';
            }
        } catch (error) {
            console.error('Failed to load conversations:', error);
            this.showError('Failed to load conversations');
        }
    }

    addConversationToSidebar(conversation) {
        const conversationElement = document.createElement('div');
        conversationElement.className = 'conversation-item';
        conversationElement.dataset.conversationId = conversation.id;
        
        const title = this.getConversationTitle(conversation);
        const createdAt = new Date(conversation.created_at).toLocaleDateString();
        
        conversationElement.innerHTML = `
            <div class="conversation-title">${title}</div>
            <div class="conversation-meta">
                <span>${createdAt}</span>
                <span class="conversation-status ${conversation.execution_status.toLowerCase()}">${conversation.execution_status}</span>
            </div>
        `;
        
        conversationElement.addEventListener('click', () => {
            this.selectConversation(conversation.id);
        });
        
        this.conversationsContainer.appendChild(conversationElement);
    }

    getConversationTitle(conversation) {
        if (conversation.initial_message && conversation.initial_message.content.length > 0) {
            const firstContent = conversation.initial_message.content[0];
            if (firstContent.text) {
                return firstContent.text.substring(0, 50) + (firstContent.text.length > 50 ? '...' : '');
            }
        }
        return `Conversation ${conversation.id.substring(0, 8)}`;
    }

    async selectConversation(conversationId) {
        if (this.currentConversationId === conversationId) return;
        
        // Close existing WebSocket
        if (this.websocket) {
            this.websocket.close();
            this.websocket = null;
        }
        
        this.currentConversationId = conversationId;
        
        // Update UI
        document.querySelectorAll('.conversation-item').forEach(item => {
            item.classList.remove('active');
        });
        
        const selectedItem = document.querySelector(`[data-conversation-id="${conversationId}"]`);
        if (selectedItem) {
            selectedItem.classList.add('active');
        }
        
        const conversation = this.conversations.get(conversationId);
        if (conversation) {
            this.conversationTitle.textContent = this.getConversationTitle(conversation);
            this.updateConversationStatus(conversation.execution_status);
            this.enableChatControls();
        }
        
        // Load conversation events and connect WebSocket
        await this.loadConversationEvents(conversationId);
        this.connectWebSocket(conversationId);
    }

    async loadConversationEvents(conversationId) {
        try {
            this.showLoading();
            const data = await this.apiRequest(`/conversations/${conversationId}/events/search?limit=100`);
            
            this.chatMessages.innerHTML = '';
            
            if (data.items && data.items.length > 0) {
                data.items.forEach(event => {
                    this.displayEvent(event);
                });
            }
            
            this.scrollToBottom();
        } catch (error) {
            console.error('Failed to load conversation events:', error);
            this.showError('Failed to load conversation history');
        } finally {
            this.hideLoading();
        }
    }

    connectWebSocket(conversationId) {
        const wsUrl = `${this.wsBaseUrl}/sockets/events/${conversationId}`;
        
        this.updateConnectionStatus('connecting');
        this.websocket = new WebSocket(wsUrl);
        
        this.websocket.onopen = () => {
            console.log('WebSocket connected');
            this.updateConnectionStatus('connected');
        };
        
        this.websocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleWebSocketMessage(data);
            } catch (error) {
                console.error('Failed to parse WebSocket message:', error);
            }
        };
        
        this.websocket.onclose = () => {
            console.log('WebSocket disconnected');
            this.updateConnectionStatus('disconnected');
            this.hideTypingIndicator();
        };
        
        this.websocket.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.updateConnectionStatus('disconnected');
            this.showError('Connection error');
        };
    }

    handleWebSocketMessage(data) {
        if (data.type === 'event') {
            this.displayEvent(data.event);
            this.scrollToBottom();
            
            // Update agent running status based on event type
            if (data.event.kind === 'agent_start') {
                this.isAgentRunning = true;
                this.showTypingIndicator();
                this.updateConversationStatus('RUNNING');
            } else if (data.event.kind === 'agent_finish' || data.event.kind === 'agent_error') {
                this.isAgentRunning = false;
                this.hideTypingIndicator();
                this.updateConversationStatus('IDLE');
            }
        }
    }

    displayEvent(event) {
        const messageElement = document.createElement('div');
        
        if (event.kind === 'message') {
            this.displayMessage(event, messageElement);
        } else {
            this.displaySystemEvent(event, messageElement);
        }
        
        this.chatMessages.appendChild(messageElement);
    }

    displayMessage(event, messageElement) {
        messageElement.className = `message ${event.role}`;
        
        const timestamp = new Date(event.timestamp).toLocaleTimeString();
        const content = event.content.map(c => c.text || c.image_url || '[Media]').join(' ');
        
        messageElement.innerHTML = `
            <div class="message-header">
                <i class="fas fa-${event.role === 'user' ? 'user' : 'robot'}"></i>
                <span>${event.role.charAt(0).toUpperCase() + event.role.slice(1)}</span>
            </div>
            <div class="message-content">${this.formatMessageContent(content)}</div>
            <div class="message-timestamp">${timestamp}</div>
        `;
    }

    displaySystemEvent(event, messageElement) {
        messageElement.className = 'event-message';
        
        let eventClass = '';
        let eventIcon = 'info-circle';
        
        switch (event.kind) {
            case 'tool_call':
                eventClass = 'tool-call';
                eventIcon = 'cog';
                break;
            case 'tool_result':
                eventClass = 'tool-result';
                eventIcon = 'check-circle';
                break;
            case 'agent_error':
                eventClass = 'error';
                eventIcon = 'exclamation-triangle';
                break;
        }
        
        if (eventClass) {
            messageElement.classList.add(eventClass);
        }
        
        const timestamp = new Date(event.timestamp).toLocaleTimeString();
        const content = this.formatEventContent(event);
        
        messageElement.innerHTML = `
            <div class="event-type">
                <i class="fas fa-${eventIcon}"></i> ${event.kind.replace('_', ' ')}
            </div>
            <div class="event-content">${content}</div>
            <div class="message-timestamp">${timestamp}</div>
        `;
    }

    formatMessageContent(content) {
        // Basic HTML escaping and formatting
        return content
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\n/g, '<br>');
    }

    formatEventContent(event) {
        let content = '';
        
        if (event.tool_name) {
            content += `<strong>Tool:</strong> ${event.tool_name}<br>`;
        }
        
        if (event.content) {
            content += this.formatMessageContent(JSON.stringify(event.content, null, 2));
        } else if (event.result) {
            content += this.formatMessageContent(JSON.stringify(event.result, null, 2));
        } else if (event.error) {
            content += `<strong>Error:</strong> ${this.formatMessageContent(event.error)}`;
        }
        
        return content || 'No additional details';
    }

    showTypingIndicator() {
        this.typingIndicator.style.display = 'flex';
    }

    hideTypingIndicator() {
        this.typingIndicator.style.display = 'none';
    }

    scrollToBottom() {
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    }

    enableChatControls() {
        this.messageInput.disabled = false;
        this.sendBtn.disabled = false;
        this.pauseBtn.disabled = false;
        this.resumeBtn.disabled = false;
        this.deleteBtn.disabled = false;
    }

    disableChatControls() {
        this.messageInput.disabled = true;
        this.sendBtn.disabled = true;
        this.pauseBtn.disabled = true;
        this.resumeBtn.disabled = true;
        this.deleteBtn.disabled = true;
    }

    updateConversationStatus(status) {
        this.conversationStatus.textContent = status;
        this.conversationStatus.className = `status-badge ${status.toLowerCase()}`;
        
        // Update conversation in sidebar
        if (this.currentConversationId) {
            const conversationItem = document.querySelector(`[data-conversation-id="${this.currentConversationId}"]`);
            if (conversationItem) {
                const statusElement = conversationItem.querySelector('.conversation-status');
                if (statusElement) {
                    statusElement.textContent = status;
                    statusElement.className = `conversation-status ${status.toLowerCase()}`;
                }
            }
        }
    }

    async sendMessage() {
        const message = this.messageInput.value.trim();
        if (!message || !this.currentConversationId) return;
        
        try {
            this.messageInput.value = '';
            this.messageInput.style.height = 'auto';
            
            await this.apiRequest(`/conversations/${this.currentConversationId}/events`, {
                method: 'POST',
                body: JSON.stringify({
                    role: 'user',
                    content: [{ type: 'text', text: message }],
                    run: true
                })
            });
            
            this.showTypingIndicator();
            this.updateConversationStatus('RUNNING');
            
        } catch (error) {
            console.error('Failed to send message:', error);
            this.showError('Failed to send message');
        }
    }

    async pauseConversation() {
        if (!this.currentConversationId) return;
        
        try {
            await this.apiRequest(`/conversations/${this.currentConversationId}/pause`, {
                method: 'POST'
            });
            this.updateConversationStatus('PAUSED');
        } catch (error) {
            console.error('Failed to pause conversation:', error);
            this.showError('Failed to pause conversation');
        }
    }

    async resumeConversation() {
        if (!this.currentConversationId) return;
        
        try {
            await this.apiRequest(`/conversations/${this.currentConversationId}/run`, {
                method: 'POST'
            });
            this.updateConversationStatus('RUNNING');
            this.showTypingIndicator();
        } catch (error) {
            console.error('Failed to resume conversation:', error);
            this.showError('Failed to resume conversation');
        }
    }

    async deleteConversation() {
        if (!this.currentConversationId) return;
        
        if (!confirm('Are you sure you want to delete this conversation? This action cannot be undone.')) {
            return;
        }
        
        try {
            await this.apiRequest(`/conversations/${this.currentConversationId}`, {
                method: 'DELETE'
            });
            
            // Remove from UI
            const conversationItem = document.querySelector(`[data-conversation-id="${this.currentConversationId}"]`);
            if (conversationItem) {
                conversationItem.remove();
            }
            
            this.conversations.delete(this.currentConversationId);
            
            // Reset UI
            this.currentConversationId = null;
            this.chatMessages.innerHTML = `
                <div class="welcome-message">
                    <div class="welcome-content">
                        <i class="fas fa-robot welcome-icon"></i>
                        <h2>Conversation Deleted</h2>
                        <p>Select another conversation or create a new one to continue.</p>
                    </div>
                </div>
            `;
            this.conversationTitle.textContent = 'Select or create a conversation';
            this.conversationStatus.textContent = 'No conversation';
            this.conversationStatus.className = 'status-badge';
            this.disableChatControls();
            
            if (this.websocket) {
                this.websocket.close();
                this.websocket = null;
            }
            
        } catch (error) {
            console.error('Failed to delete conversation:', error);
            this.showError('Failed to delete conversation');
        }
    }

    // Local storage functions for dialog settings
    saveDialogSettings() {
        const settings = {
            initialMessage: this.initialMessageInput.value,
            jsonParameters: this.jsonParametersInput.value
        };
        localStorage.setItem('openhandsDialogSettings', JSON.stringify(settings));
    }

    loadDialogSettings() {
        try {
            const saved = localStorage.getItem('openhandsDialogSettings');
            if (saved) {
                const settings = JSON.parse(saved);
                this.initialMessageInput.value = settings.initialMessage || '';
                this.jsonParametersInput.value = settings.jsonParameters || '';
                this.validateJsonParameters();
            } else {
                // If no saved settings, use the first example from START_CONVERSATION_EXAMPLES
                this.jsonParametersInput.value = this.getDefaultJsonParameters();
                this.validateJsonParameters();
            }
        } catch (error) {
            console.warn('Failed to load dialog settings from localStorage:', error);
            // Fallback to default if localStorage fails
            this.jsonParametersInput.value = this.getDefaultJsonParameters();
            this.validateJsonParameters();
        }
    }

    getDefaultJsonParameters() {
        // Based on the first example from START_CONVERSATION_EXAMPLES (without initial_message)
        return JSON.stringify({
            agent: {
                llm: {
                    model: "litellm_proxy/anthropic/claude-sonnet-4-5-20250929",
                    base_url: "https://llm-proxy.app.all-hands.dev",
                    api_key: "secret"
                },
                tools: [
                    { "name": "terminal" },
                    { "name": "file_editor" },
                    { "name": "task_tracker" },
                    { "name": "browser_tool_set" }
                ]
            },
            workspace: {
                kind: "LocalWorkspace",
                working_dir: "workspace/project"
            }
        }, null, 2);
    }

    resetJsonParameters() {
        this.jsonParametersInput.value = this.getDefaultJsonParameters();
        this.validateJsonParameters();
    }

    showJsonExample() {
        const example = this.getDefaultJsonParameters();
        if (!this.jsonParametersInput.value.trim()) {
            this.jsonParametersInput.value = example;
            this.validateJsonParameters();
        } else {
            // Show example in a simple alert for now
            alert('Example JSON Parameters:\n\n' + example);
        }
    }

    validateJsonParameters() {
        const jsonText = this.jsonParametersInput.value.trim();
        
        // Clear previous error
        this.jsonValidationError.style.display = 'none';
        this.jsonParametersInput.style.borderColor = '';
        
        if (!jsonText) {
            return true; // Empty is valid (will use defaults)
        }
        
        try {
            JSON.parse(jsonText);
            return true;
        } catch (error) {
            this.jsonValidationError.textContent = `Invalid JSON: ${error.message}`;
            this.jsonValidationError.style.display = 'block';
            this.jsonParametersInput.style.borderColor = '#e74c3c';
            return false;
        }
    }

    showNewConversationModal() {
        this.loadDialogSettings();
        this.newConversationModal.style.display = 'block';
        this.initialMessageInput.focus();
    }

    hideNewConversationModal() {
        this.newConversationModal.style.display = 'none';
        this.newConversationForm.reset();
    }

    async createNewConversation() {
        // Validate JSON parameters first
        if (!this.validateJsonParameters()) {
            return;
        }

        const initialMessage = this.initialMessageInput.value.trim();
        const jsonParameters = this.jsonParametersInput.value.trim();
        
        try {
            this.showLoading();
            
            let requestBody;
            
            if (jsonParameters) {
                // Use custom JSON parameters
                try {
                    requestBody = JSON.parse(jsonParameters);
                } catch (error) {
                    this.showError('Invalid JSON parameters: ' + error.message);
                    return;
                }
            } else {
                // Use default parameters based on START_CONVERSATION_EXAMPLES
                requestBody = JSON.parse(this.getDefaultJsonParameters());
            }
            
            // Always build initial_message from UI input if provided
            if (initialMessage) {
                requestBody.initial_message = {
                    role: "user",
                    content: [{ type: "text", text: initialMessage }],
                    run: true
                };
            }
            
            const response = await this.apiRequest('/conversations', {
                method: 'POST',
                body: JSON.stringify(requestBody)
            });
            
            // Save settings to localStorage
            this.saveDialogSettings();
            
            this.hideNewConversationModal();
            
            // Reload conversations and select the new one
            await this.loadConversations();
            
            if (response.conversation_id) {
                this.selectConversation(response.conversation_id);
            }
            
        } catch (error) {
            console.error('Failed to create conversation:', error);
            this.showError('Failed to create conversation. Please check your API configuration.');
        } finally {
            this.hideLoading();
        }
    }

    showError(message) {
        // Simple error display - in a real app you might want a more sophisticated notification system
        const errorDiv = document.createElement('div');
        errorDiv.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #e74c3c;
            color: white;
            padding: 15px 20px;
            border-radius: 6px;
            z-index: 1000;
            max-width: 300px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        `;
        errorDiv.innerHTML = `
            <div style="display: flex; align-items: center; gap: 10px;">
                <i class="fas fa-exclamation-triangle"></i>
                <span>${message}</span>
            </div>
        `;
        
        document.body.appendChild(errorDiv);
        
        setTimeout(() => {
            if (errorDiv.parentNode) {
                errorDiv.parentNode.removeChild(errorDiv);
            }
        }, 5000);
    }
}

// Initialize the application when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new OpenHandsWebChat();
});
