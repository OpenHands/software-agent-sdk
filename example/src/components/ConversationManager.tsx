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

  const getStatusColorClass = (status?: string) => {
    switch (status) {
      case 'running': return 'text-green-500';
      case 'stopped': return 'text-red-500';
      case 'paused': return 'text-orange-500';
      default: return 'text-gray-500';
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
                          ● {conversation.status || 'unknown'}
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
                      ● {selectedConversation.status || 'unknown'}
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
                  <button 
                    onClick={sendMessage}
                    disabled={!messageInput.trim() || loading}
                    className="w-full bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-md font-medium transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
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