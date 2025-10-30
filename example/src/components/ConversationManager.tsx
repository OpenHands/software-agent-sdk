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

interface ConversationWithDetails extends ConversationInfo {
  latestEvent?: Event;
  isLoading?: boolean;
}

export const ConversationManager: React.FC = () => {
  const { settings } = useSettings();
  const [conversations, setConversations] = useState<ConversationWithDetails[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manager, setManager] = useState<SDKConversationManager | null>(null);
  const [selectedConversation, setSelectedConversation] = useState<string | null>(null);
  const [messageInput, setMessageInput] = useState('');
  const [activeConversations, setActiveConversations] = useState<Map<string, RemoteConversation>>(new Map());

  // Initialize conversation manager
  useEffect(() => {
    if (settings.agentServerUrl) {
      const newManager = new SDKConversationManager({
        host: settings.agentServerUrl,
        apiKey: settings.agentServerApiKey || undefined,
      });
      setManager(newManager);

      return () => {
        newManager.close();
      };
    }
  }, [settings.agentServerUrl, settings.agentServerApiKey]);

  // Load conversations
  const loadConversations = async () => {
    if (!manager) return;

    setLoading(true);
    setError(null);
    try {
      const conversationList = await manager.getAllConversations();
      setConversations(conversationList.map(conv => ({ ...conv, isLoading: false })));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load conversations');
    } finally {
      setLoading(false);
    }
  };

  // Load conversations when manager is ready
  useEffect(() => {
    if (manager) {
      loadConversations();
    }
  }, [manager]);

  // Create new conversation
  const createConversation = async () => {
    if (!manager) return;

    setLoading(true);
    setError(null);
    try {
      const agent: AgentBase = {
        name: 'CodeActAgent',
        llm: {
          model: settings.modelName,
          api_key: settings.apiKey,
        },
      };

      const conversation = await manager.createConversation(agent, {
        initialMessage: 'Hello! I\'m ready to help you with your tasks.',
        maxIterations: 50,
        stuckDetection: true,
      });

      // Add to active conversations
      setActiveConversations(prev => new Map(prev.set(conversation.id, conversation)));

      // Reload conversations list
      await loadConversations();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create conversation');
    } finally {
      setLoading(false);
    }
  };

  // Delete conversation
  const deleteConversation = async (conversationId: string) => {
    if (!manager) return;

    if (!confirm('Are you sure you want to delete this conversation?')) {
      return;
    }

    setLoading(true);
    setError(null);
    try {
      await manager.deleteConversation(conversationId);
      
      // Remove from active conversations
      const activeConv = activeConversations.get(conversationId);
      if (activeConv) {
        await activeConv.close();
        setActiveConversations(prev => {
          const newMap = new Map(prev);
          newMap.delete(conversationId);
          return newMap;
        });
      }

      // Clear selection if this conversation was selected
      if (selectedConversation === conversationId) {
        setSelectedConversation(null);
      }

      // Reload conversations list
      await loadConversations();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete conversation');
    } finally {
      setLoading(false);
    }
  };

  // Send message to conversation
  const sendMessage = async (conversationId: string, message: string) => {
    if (!manager || !message.trim()) return;

    setError(null);
    try {
      let conversation = activeConversations.get(conversationId);
      
      if (!conversation) {
        // Load the conversation if not already active
        conversation = await manager.loadConversation(conversationId);
        setActiveConversations(prev => new Map(prev.set(conversationId, conversation!)));
      }

      await conversation.sendMessage(message);
      await conversation.run();
      
      setMessageInput('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message');
    }
  };

  // Get status color
  const getStatusColor = (status: AgentExecutionStatus): string => {
    switch (status) {
      case AgentExecutionStatus.IDLE:
        return '#6b7280';
      case AgentExecutionStatus.RUNNING:
        return '#3b82f6';
      case AgentExecutionStatus.PAUSED:
        return '#f59e0b';
      case AgentExecutionStatus.FINISHED:
        return '#10b981';
      case AgentExecutionStatus.ERROR:
        return '#ef4444';
      default:
        return '#6b7280';
    }
  };

  // Format timestamp (for future use)
  // const formatTimestamp = (timestamp: string): string => {
  //   return new Date(timestamp).toLocaleString();
  // };

  if (!settings.agentServerUrl || !settings.apiKey) {
    return (
      <div className="conversation-manager">
        <div className="empty-state">
          <h2>Configuration Required</h2>
          <p>Please configure your settings to start using the conversation manager.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="conversation-manager">
      <div className="conversation-header">
        <h1>Conversation Manager</h1>
        <div className="header-actions">
          <button 
            onClick={loadConversations} 
            disabled={loading}
            className="refresh-button"
          >
            🔄 Refresh
          </button>
          <button 
            onClick={createConversation} 
            disabled={loading}
            className="create-button"
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

      {loading && (
        <div className="loading-message">
          Loading conversations...
        </div>
      )}

      <div className="conversation-layout">
        <div className="conversation-list">
          <h2>Conversations ({conversations.length})</h2>
          {conversations.length === 0 && !loading ? (
            <div className="empty-list">
              <p>No conversations yet. Create your first conversation!</p>
            </div>
          ) : (
            <div className="conversation-items">
              {conversations.map((conversation) => (
                <div 
                  key={conversation.id} 
                  className={`conversation-item ${selectedConversation === conversation.id ? 'selected' : ''}`}
                  onClick={() => setSelectedConversation(conversation.id)}
                >
                  <div className="conversation-info">
                    <div className="conversation-id">
                      ID: {conversation.id.substring(0, 8)}...
                    </div>
                    <div className="conversation-status">
                      <span 
                        className="status-indicator"
                        style={{ backgroundColor: getStatusColor(conversation.agent_status) }}
                      ></span>
                      Status: {conversation.agent_status}
                    </div>
                    <div className="conversation-stats">
                      Events: {conversation.conversation_stats?.total_events || 0} | 
                      Messages: {conversation.conversation_stats?.message_events || 0}
                    </div>
                    <div className="conversation-agent">
                      Agent: {conversation.agent?.name || 'Unknown'}
                    </div>
                  </div>
                  <div className="conversation-actions">
                    <button 
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteConversation(conversation.id);
                      }}
                      className="delete-button"
                      title="Delete conversation"
                    >
                      🗑️
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {selectedConversation && (
          <div className="conversation-detail">
            <h2>Conversation Details</h2>
            <div className="conversation-info-detail">
              {(() => {
                const conv = conversations.find(c => c.id === selectedConversation);
                if (!conv) return null;
                
                return (
                  <div>
                    <p><strong>ID:</strong> {conv.id}</p>
                    <p><strong>Status:</strong> 
                      <span 
                        className="status-indicator"
                        style={{ backgroundColor: getStatusColor(conv.agent_status) }}
                      ></span>
                      {conv.agent_status}
                    </p>
                    <p><strong>Agent:</strong> {conv.agent?.name}</p>
                    <p><strong>Model:</strong> {conv.agent?.llm?.model}</p>
                    <p><strong>Total Events:</strong> {conv.conversation_stats?.total_events || 0}</p>
                    <p><strong>Messages:</strong> {conv.conversation_stats?.message_events || 0}</p>
                    <p><strong>Actions:</strong> {conv.conversation_stats?.action_events || 0}</p>
                    <p><strong>Observations:</strong> {conv.conversation_stats?.observation_events || 0}</p>
                  </div>
                );
              })()}
            </div>

            <div className="message-input-section">
              <h3>Send Message</h3>
              <div className="message-input-container">
                <textarea
                  value={messageInput}
                  onChange={(e) => setMessageInput(e.target.value)}
                  placeholder="Type your message here..."
                  className="message-input"
                  rows={3}
                />
                <button
                  onClick={() => sendMessage(selectedConversation, messageInput)}
                  disabled={!messageInput.trim() || loading}
                  className="send-button"
                >
                  Send Message
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};