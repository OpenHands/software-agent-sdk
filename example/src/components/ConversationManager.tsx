import React, { useState, useEffect } from 'react';
import { 
  ConversationManager as SDKConversationManager,
  ConversationInfo,
  RemoteConversation,
  AgentBase,
  AgentExecutionStatus,
  Event
} from '@openhands/agent-server-typescript-client';
import { useSettings } from '../contexts/SettingsContext';
import './ConversationManager.css';

interface ConversationData extends ConversationInfo {
  remoteConversation?: RemoteConversation;
  events?: Event[];
  agentStatus?: AgentExecutionStatus;
}

export const ConversationManager: React.FC = () => {
  const { settings } = useSettings();
  const [conversations, setConversations] = useState<ConversationData[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manager, setManager] = useState<SDKConversationManager | null>(null);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [messageInput, setMessageInput] = useState('');

  // Get selected conversation data
  const selectedConversation = conversations.find(c => c.id === selectedConversationId);

  // Initialize conversation manager
  useEffect(() => {
    if (settings.agentServerUrl) {
      const conversationManager = new SDKConversationManager({
        host: settings.agentServerUrl,
        apiKey: settings.apiKey
      });
      setManager(conversationManager);
      loadConversations(conversationManager);
    }
  }, [settings.agentServerUrl, settings.apiKey]);

  const loadConversations = async (conversationManager?: SDKConversationManager) => {
    const mgr = conversationManager || manager;
    if (!mgr) return;

    setLoading(true);
    setError(null);
    try {
      const conversationList = await mgr.getAllConversations();
      console.log('Loaded conversations:', conversationList);
      
      // Convert to our data structure
      const conversationData: ConversationData[] = conversationList.map((conv: ConversationInfo) => ({
        ...conv,
        // Ensure we have the basic properties
        id: conv.id,
        agent: conv.agent,
        created_at: conv.created_at,
        updated_at: conv.updated_at,
        status: conv.status
      }));
      
      setConversations(conversationData);
    } catch (err) {
      console.error('Failed to load conversations:', err);
      setError(err instanceof Error ? err.message : 'Failed to load conversations');
    } finally {
      setLoading(false);
    }
  };

  const createConversation = async () => {
    if (!manager) return;

    setLoading(true);
    setError(null);
    try {
      // Create a simple agent configuration
      const agent: AgentBase = {
        name: 'CodeActAgent',
        llm: {
          model: 'gpt-4o-mini',
          api_key: settings.apiKey || '',
          base_url: 'https://api.openai.com/v1'
        }
      };

      const conversation = await manager.createConversation(agent, {
        initialMessage: 'Hello! I\'m ready to help you with your tasks.',
        maxIterations: 50,
      });

      console.log('Created conversation:', conversation);
      
      // Reload conversations to show the new one
      await loadConversations();
    } catch (err) {
      console.error('Failed to create conversation:', err);
      setError(err instanceof Error ? err.message : 'Failed to create conversation');
    } finally {
      setLoading(false);
    }
  };

  const deleteConversation = async (conversationId: string) => {
    if (!manager) return;

    setLoading(true);
    setError(null);
    try {
      await manager.deleteConversation(conversationId);
      
      // Clear selection if this conversation was selected
      if (selectedConversationId === conversationId) {
        setSelectedConversationId(null);
      }

      // Reload conversations list
      await loadConversations();
    } catch (err) {
      console.error('Failed to delete conversation:', err);
      setError(err instanceof Error ? err.message : 'Failed to delete conversation');
    } finally {
      setLoading(false);
    }
  };

  const selectConversation = async (conversationId: string) => {
    if (!manager) return;

    console.log('Selecting conversation:', conversationId);
    setSelectedConversationId(conversationId);
    
    // Load conversation details
    try {
      const remoteConversation = await manager.loadConversation(conversationId);
      console.log('Loaded remote conversation:', remoteConversation);
      
      // Get events
      const events = await remoteConversation.state.events.getEvents();
      console.log('Loaded events:', events);
      
      // Get agent status
      const agentStatus = await remoteConversation.state.getAgentStatus();
      console.log('Agent status:', agentStatus);
      
      // Update the conversation in our state with additional details
      setConversations(prev => prev.map(conv => 
        conv.id === conversationId 
          ? { ...conv, remoteConversation, events, agentStatus }
          : conv
      ));
      
    } catch (err) {
      console.error('Failed to load conversation details:', err);
      setError(err instanceof Error ? err.message : 'Failed to load conversation details');
    }
  };

  const sendMessage = async () => {
    if (!selectedConversation?.remoteConversation || !messageInput.trim()) return;

    try {
      await selectedConversation.remoteConversation.sendMessage(messageInput);
      setMessageInput('');
      
      // Reload conversation details to show new events
      await selectConversation(selectedConversation.id);
    } catch (err) {
      console.error('Failed to send message:', err);
      setError(err instanceof Error ? err.message : 'Failed to send message');
    }
  };

  const formatDate = (dateString?: string) => {
    if (!dateString) return 'Unknown';
    return new Date(dateString).toLocaleString();
  };

  const getAgentName = (agent: AgentBase) => {
    return agent.name || 'Unknown Agent';
  };

  const getStatusColor = (status?: string) => {
    switch (status) {
      case 'running': return '#4CAF50';
      case 'stopped': return '#f44336';
      case 'paused': return '#ff9800';
      default: return '#9e9e9e';
    }
  };

  return (
    <div className="conversation-manager">
      <div className="conversation-header">
        <h2>Conversation Manager</h2>
        <div className="header-actions">
          <button 
            onClick={() => loadConversations()} 
            disabled={loading}
            className="refresh-btn"
          >
            🔄 Refresh
          </button>
          <button 
            onClick={createConversation} 
            disabled={loading}
            className="create-btn"
          >
            ➕ New Conversation
          </button>
        </div>
      </div>

      {error && (
        <div className="error-message">
          <strong>Error:</strong> {error}
        </div>
      )}

      <div className="conversation-content">
        <div className="conversation-list">
          <h3>Conversations ({conversations.length})</h3>
          
          {loading && conversations.length === 0 && (
            <div className="loading">Loading conversations...</div>
          )}
          
          {conversations.length === 0 && !loading ? (
            <div className="empty-list">
              <p>No conversations yet. Create your first conversation!</p>
            </div>
          ) : (
            <div className="conversation-items">
              {conversations.map((conversation) => (
                <div 
                  key={conversation.id} 
                  className={`conversation-item ${selectedConversationId === conversation.id ? 'selected' : ''}`}
                  onClick={() => selectConversation(conversation.id)}
                >
                  <div className="conversation-info">
                    <div className="conversation-id">
                      ID: {conversation.id.substring(0, 8)}...
                    </div>
                    <div className="conversation-details">
                      <div>Agent: {getAgentName(conversation.agent)}</div>
                      <div>Created: {formatDate(conversation.created_at)}</div>
                      <div className="conversation-status">
                        Status: <span 
                          className="status-indicator" 
                          style={{ color: getStatusColor(conversation.status) }}
                        >
                          ● {conversation.status || 'unknown'}
                        </span>
                      </div>
                    </div>
                  </div>
                  <button 
                    className="delete-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteConversation(conversation.id);
                    }}
                    disabled={loading}
                  >
                    🗑️
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="conversation-details">
          <h3>Conversation Details</h3>
          
          {!selectedConversation ? (
            <div className="no-selection">
              <p>Select a conversation to view details</p>
            </div>
          ) : (
            <div className="details-content">
              <div className="details-info">
                <div className="detail-row">
                  <strong>ID:</strong> {selectedConversation.id}
                </div>
                <div className="detail-row">
                  <strong>Status:</strong> 
                  <span 
                    className="status-indicator" 
                    style={{ color: getStatusColor(selectedConversation.status) }}
                  >
                    ● {selectedConversation.status || 'unknown'}
                  </span>
                </div>
                <div className="detail-row">
                  <strong>Agent:</strong> {getAgentName(selectedConversation.agent)}
                </div>
                <div className="detail-row">
                  <strong>Model:</strong> {selectedConversation.agent.llm?.model || 'Unknown'}
                </div>
                <div className="detail-row">
                  <strong>Created:</strong> {formatDate(selectedConversation.created_at)}
                </div>
                <div className="detail-row">
                  <strong>Updated:</strong> {formatDate(selectedConversation.updated_at)}
                </div>
                {selectedConversation.agentStatus && (
                  <div className="detail-row">
                    <strong>Agent Status:</strong> {selectedConversation.agentStatus}
                  </div>
                )}
                <div className="detail-row">
                  <strong>Total Events:</strong> {selectedConversation.events?.length || 0}
                </div>
              </div>

              <div className="events-section">
                <h4>Events & Messages</h4>
                <div className="events-list">
                  {selectedConversation.events && selectedConversation.events.length > 0 ? (
                    selectedConversation.events.map((event, index) => (
                      <div key={index} className="event-item">
                        <div className="event-header">
                          <span className="event-type">{event.type}</span>
                          <span className="event-timestamp">
                            {event.timestamp ? new Date(event.timestamp).toLocaleTimeString() : ''}
                          </span>
                        </div>
                        {event.message && (
                          <div className="event-message">{event.message}</div>
                        )}
                        {event.content && (
                          <div className="event-content">{JSON.stringify(event.content, null, 2)}</div>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="no-events">No events yet</div>
                  )}
                </div>
              </div>

              <div className="message-section">
                <h4>Send Message</h4>
                <div className="message-input-container">
                  <textarea
                    value={messageInput}
                    onChange={(e) => setMessageInput(e.target.value)}
                    placeholder="Type your message here..."
                    className="message-input"
                    rows={3}
                  />
                  <button 
                    onClick={sendMessage}
                    disabled={!messageInput.trim() || loading}
                    className="send-btn"
                  >
                    Send Message
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ConversationManager;