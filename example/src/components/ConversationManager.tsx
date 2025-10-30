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
        apiKey: settings.agentServerApiKey
      });
      setManager(conversationManager);
      loadConversations(conversationManager);
    }
  }, [settings.agentServerUrl, settings.agentServerApiKey]);

  // Cleanup WebSocket connections on unmount
  useEffect(() => {
    return () => {
      if (selectedConversation?.remoteConversation) {
        selectedConversation.remoteConversation.stopWebSocketClient().catch(err => {
          console.warn('Failed to stop WebSocket client on unmount:', err);
        });
      }
    };
  }, [selectedConversation?.remoteConversation]);

  // Periodic status refresh for running conversations
  useEffect(() => {
    if (!selectedConversation?.remoteConversation) return;

    const refreshStatus = async () => {
      try {
        const status = await selectedConversation.remoteConversation!.state.getAgentStatus();
        setConversations(prev => prev.map(conv => 
          conv.id === selectedConversation.id 
            ? { ...conv, agentStatus: status }
            : conv
        ));
      } catch (err) {
        console.warn('Failed to refresh agent status:', err);
      }
    };

    // Refresh every 2 seconds if the agent is running
    const interval = setInterval(() => {
      if (selectedConversation.agentStatus === 'running') {
        refreshStatus();
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [selectedConversation?.id, selectedConversation?.agentStatus]);

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
          model: settings.modelName,
          api_key: settings.apiKey || ''
        }
      };

      const conversation = await RemoteConversation.create(
        manager.host,
        agent,
        {
          apiKey: manager.apiKey,
          initialMessage: 'Hello! I\'m ready to help you with your tasks.',
          maxIterations: 50,
          callback: (event: Event) => {
            console.log('Received WebSocket event for new conversation:', event);
            
            // Update the conversation's events in real-time
            setConversations(prev => prev.map(conv => {
              if (conv.id === conversation.id && conv.events) {
                const updatedEvents = [...conv.events, event];
                return { ...conv, events: updatedEvents };
              }
              return conv;
            }));
          },
        }
      );

      console.log('Created conversation:', conversation);
      
      // Start WebSocket client for real-time updates
      try {
        await conversation.startWebSocketClient();
        console.log('WebSocket client started for new conversation');
      } catch (wsErr) {
        console.warn('Failed to start WebSocket client for new conversation:', wsErr);
      }
      
      // Run the initial message to start the conversation
      try {
        await conversation.run();
        console.log('Started conversation with initial message');
      } catch (runErr) {
        console.warn('Failed to run initial message:', runErr);
      }
      
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

    // Clean up previous conversation's WebSocket if any
    if (selectedConversation?.remoteConversation) {
      try {
        await selectedConversation.remoteConversation.stopWebSocketClient();
        console.log('Stopped WebSocket client for previous conversation');
      } catch (err) {
        console.warn('Failed to stop previous WebSocket client:', err);
      }
    }

    console.log('Selecting conversation:', conversationId);
    setSelectedConversationId(conversationId);
    
    // Load conversation details
    try {
      // Create a callback to handle real-time events
      const eventCallback = (event: Event) => {
        console.log('Received WebSocket event:', event);
        
        // Update the conversation's events in real-time
        setConversations(prev => prev.map(conv => {
          if (conv.id === conversationId && conv.events) {
            // Add the new event to the existing events
            const updatedEvents = [...conv.events, event];
            return { ...conv, events: updatedEvents };
          }
          return conv;
        }));
        
        // If it's a status change event, update the agent status
        if (event.type === 'agent_status_change' || event.type === 'agent_state_changed') {
          // Refresh agent status after a short delay to ensure the server has updated
          setTimeout(() => {
            if (remoteConversation) {
              remoteConversation.state.getAgentStatus().then(status => {
                setConversations(prev => prev.map(conv => 
                  conv.id === conversationId 
                    ? { ...conv, agentStatus: status }
                    : conv
                ));
              }).catch(err => console.warn('Failed to update agent status:', err));
            }
          }, 100);
        }
      };
      
      // Load conversation with callback
      const remoteConversation = await RemoteConversation.load(
        manager.host,
        conversationId,
        {
          apiKey: manager.apiKey,
          callback: eventCallback,
        }
      );
      console.log('Loaded remote conversation:', remoteConversation);
      
      // Start WebSocket client for real-time updates
      try {
        await remoteConversation.startWebSocketClient();
        console.log('WebSocket client started for conversation:', conversationId);
      } catch (wsErr) {
        console.warn('Failed to start WebSocket client:', wsErr);
        // Don't fail the whole operation if WebSocket fails
      }
      
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
      // Send the message
      await selectedConversation.remoteConversation.sendMessage(messageInput);
      setMessageInput('');
      
      // Start the agent to process the message (non-blocking)
      await selectedConversation.remoteConversation.run();
      console.log('Agent started to process the message');
      
      // The WebSocket will receive events as the agent works
      // No need to reload immediately - events will come via WebSocket
    } catch (err) {
      console.error('Failed to send message or start agent:', err);
      setError(err instanceof Error ? err.message : 'Failed to send message or start agent');
    }
  };

  const formatDate = (dateString?: string) => {
    if (!dateString) return 'Unknown';
    return new Date(dateString).toLocaleString();
  };

  const getAgentName = (agent: AgentBase) => {
    return agent.name || 'Unknown Agent';
  };

  const getStatusColorClass = (status?: string) => {
    switch (status) {
      case 'running': return 'text-green-500';
      case 'stopped': return 'text-red-500';
      case 'paused': return 'text-orange-500';
      case 'finished': return 'text-blue-500';
      case 'error': return 'text-red-600';
      default: return 'text-gray-500';
    }
  };

  const getStatusIcon = (status?: string) => {
    switch (status) {
      case 'running': return '🔄';
      case 'stopped': return '⏹️';
      case 'paused': return '⏸️';
      case 'finished': return '✅';
      case 'error': return '❌';
      default: return '❓';
    }
  };

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-lg p-6 m-0">
      <div className="flex justify-between items-center mb-6 pb-4 border-b border-gray-200 dark:border-gray-700">
        <h2 className="text-2xl font-bold text-gray-900 dark:text-white m-0">Conversation Manager</h2>
        <div className="flex gap-3">
          <button 
            onClick={() => loadConversations()} 
            disabled={loading}
            className="bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 px-4 py-2 rounded-md font-medium transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed border border-gray-300 dark:border-gray-600"
          >
            🔄 Refresh
          </button>
          <button 
            onClick={createConversation} 
            disabled={loading}
            className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-md font-medium transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            ➕ New Conversation
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 rounded-md p-4 mb-4">
          <span className="text-red-700 dark:text-red-300 font-medium">Error:</span> 
          <span className="text-red-600 dark:text-red-400 ml-2">{error}</span>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-50 dark:bg-gray-900 rounded-lg p-4">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Conversations ({conversations.length})</h3>
          
          {loading && conversations.length === 0 && (
            <div className="text-center py-8 text-gray-500 dark:text-gray-400">Loading conversations...</div>
          )}
          
          {conversations.length === 0 && !loading ? (
            <div className="text-center py-8 text-gray-500 dark:text-gray-400">
              <p>No conversations yet. Create your first conversation!</p>
            </div>
          ) : (
            <div className="space-y-3 max-h-96 overflow-y-auto">
              {conversations.map((conversation) => (
                <div 
                  key={conversation.id} 
                  className={`border rounded-lg p-3 cursor-pointer transition-all duration-200 flex justify-between items-start ${
                    selectedConversationId === conversation.id 
                      ? 'border-indigo-500 bg-indigo-50 dark:bg-indigo-900/30 shadow-md' 
                      : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600 hover:shadow-sm'
                  }`}
                  onClick={() => selectConversation(conversation.id)}
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-mono text-gray-600 dark:text-gray-400 mb-2">
                      ID: {conversation.id.substring(0, 8)}...
                    </div>
                    <div className="space-y-1 text-sm">
                      <div className="text-gray-900 dark:text-white">Agent: {getAgentName(conversation.agent)}</div>
                      <div className="text-gray-600 dark:text-gray-400">Created: {formatDate(conversation.created_at)}</div>
                      <div className="flex items-center gap-2">
                        <span className="text-gray-600 dark:text-gray-400">Status:</span>
                        <span className={`font-medium ${getStatusColorClass(conversation.status)}`}>
                          {getStatusIcon(conversation.status)} {conversation.status || 'unknown'}
                        </span>
                      </div>
                    </div>
                  </div>
                  <button 
                    className="ml-3 p-2 text-red-500 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/30 rounded-md transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteConversation(conversation.id);
                    }}
                    disabled={loading}
                    title="Delete conversation"
                  >
                    🗑️
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="bg-gray-50 dark:bg-gray-900 rounded-lg p-4">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Conversation Details</h3>
          
          {!selectedConversation ? (
            <div className="text-center py-8 text-gray-500 dark:text-gray-400">
              <p>Select a conversation to view details</p>
            </div>
          ) : (
            <div className="space-y-6">
              <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
                <div className="grid grid-cols-1 gap-3 text-sm">
                  <div className="flex justify-between">
                    <span className="font-medium text-gray-900 dark:text-white">ID:</span>
                    <span className="text-gray-600 dark:text-gray-400 font-mono text-xs">{selectedConversation.id}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="font-medium text-gray-900 dark:text-white">Status:</span>
                    <span className={`font-medium ${getStatusColorClass(selectedConversation.status)}`}>
                      {getStatusIcon(selectedConversation.status)} {selectedConversation.status || 'unknown'}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="font-medium text-gray-900 dark:text-white">Agent:</span>
                    <span className="text-gray-600 dark:text-gray-400">{getAgentName(selectedConversation.agent)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="font-medium text-gray-900 dark:text-white">Model:</span>
                    <span className="text-gray-600 dark:text-gray-400">{selectedConversation.agent.llm?.model || 'Unknown'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="font-medium text-gray-900 dark:text-white">Created:</span>
                    <span className="text-gray-600 dark:text-gray-400">{formatDate(selectedConversation.created_at)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="font-medium text-gray-900 dark:text-white">Updated:</span>
                    <span className="text-gray-600 dark:text-gray-400">{formatDate(selectedConversation.updated_at)}</span>
                  </div>
                  {selectedConversation.agentStatus && (
                    <div className="flex justify-between">
                      <span className="font-medium text-gray-900 dark:text-white">Agent Status:</span>
                      <span className="text-gray-600 dark:text-gray-400">{selectedConversation.agentStatus}</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="font-medium text-gray-900 dark:text-white">Total Events:</span>
                    <span className="text-gray-600 dark:text-gray-400">{selectedConversation.events?.length || 0}</span>
                  </div>
                </div>
              </div>

              <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
                <h4 className="text-base font-semibold text-gray-900 dark:text-white mb-3">Events & Messages</h4>
                <div className="max-h-64 overflow-y-auto space-y-3">
                  {selectedConversation.events && selectedConversation.events.length > 0 ? (
                    selectedConversation.events.map((event, index) => (
                      <div key={index} className="border border-gray-200 dark:border-gray-700 rounded-lg p-3 bg-gray-50 dark:bg-gray-900">
                        <div className="flex justify-between items-center mb-2">
                          <span className="text-sm font-medium text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-900/30 px-2 py-1 rounded">
                            {event.type}
                          </span>
                          <span className="text-xs text-gray-500 dark:text-gray-400">
                            {event.timestamp ? new Date(event.timestamp).toLocaleTimeString() : ''}
                          </span>
                        </div>
                        {event.message && (
                          <div className="text-sm text-gray-900 dark:text-white mb-2 p-2 bg-white dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700">
                            {event.message}
                          </div>
                        )}
                        {event.content && (
                          <div className="text-xs text-gray-600 dark:text-gray-400 font-mono bg-gray-100 dark:bg-gray-800 p-2 rounded border border-gray-200 dark:border-gray-700 overflow-x-auto">
                            <pre>{JSON.stringify(event.content, null, 2)}</pre>
                          </div>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="text-center py-4 text-gray-500 dark:text-gray-400">No events yet</div>
                  )}
                </div>
              </div>

              <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
                <h4 className="text-base font-semibold text-gray-900 dark:text-white mb-3">Send Message</h4>
                <div className="space-y-3">
                  <textarea
                    value={messageInput}
                    onChange={(e) => setMessageInput(e.target.value)}
                    placeholder="Type your message here..."
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:border-indigo-600 focus:shadow-md transition-all duration-200 resize-vertical"
                    rows={3}
                  />
                  <div className="flex gap-2">
                    <button 
                      onClick={sendMessage}
                      disabled={!messageInput.trim() || loading}
                      className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-md font-medium transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      Send & Run
                    </button>
                    <button 
                      onClick={async () => {
                        if (selectedConversation?.remoteConversation) {
                          try {
                            await selectedConversation.remoteConversation.run();
                            console.log('Agent started manually');
                            // Reload conversation details
                            await selectConversation(selectedConversation.id);
                          } catch (err) {
                            console.error('Failed to start agent:', err);
                            setError(err instanceof Error ? err.message : 'Failed to start agent');
                          }
                        }
                      }}
                      disabled={loading}
                      className="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-md font-medium transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Start/Resume the agent"
                    >
                      ▶️ Run
                    </button>
                    <button 
                      onClick={async () => {
                        if (selectedConversation?.remoteConversation) {
                          try {
                            await selectedConversation.remoteConversation.pause();
                            console.log('Agent paused manually');
                            // Reload conversation details
                            await selectConversation(selectedConversation.id);
                          } catch (err) {
                            console.error('Failed to pause agent:', err);
                            setError(err instanceof Error ? err.message : 'Failed to pause agent');
                          }
                        }
                      }}
                      disabled={loading}
                      className="bg-orange-600 hover:bg-orange-700 text-white px-4 py-2 rounded-md font-medium transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                      title="Pause the agent"
                    >
                      ⏸️ Pause
                    </button>
                  </div>
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