import React, { useState, useEffect } from 'react';
import { Settings } from './SettingsModal';
import { ServerStatus as ServerStatusType, getServerStatus } from '../utils/serverStatus';
import './ServerStatus.css';

interface ServerStatusProps {
  settings: Settings;
  onRefresh?: () => void;
}

export const ServerStatus: React.FC<ServerStatusProps> = ({ settings, onRefresh }) => {
  const [status, setStatus] = useState<ServerStatusType | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const checkStatus = async () => {
    setIsLoading(true);
    try {
      const newStatus = await getServerStatus(settings);
      setStatus(newStatus);
      onRefresh?.();
    } catch (error) {
      console.error('Failed to check server status:', error);
      setStatus({
        isConnected: false,
        connectionError: 'Failed to check server status',
        llmStatus: 'error',
        llmError: 'Status check failed',
        lastChecked: new Date(),
      });
    } finally {
      setIsLoading(false);
    }
  };

  // Initial status check
  useEffect(() => {
    checkStatus();
  }, [settings.agentServerUrl, settings.agentServerApiKey, settings.apiKey, settings.modelName]);

  // Auto-refresh functionality
  useEffect(() => {
    if (!autoRefresh) return;

    const interval = setInterval(() => {
      checkStatus();
    }, 30000); // Refresh every 30 seconds

    return () => clearInterval(interval);
  }, [autoRefresh, settings]);

  const getConnectionStatusIcon = () => {
    if (isLoading) return '⏳';
    return status?.isConnected ? '🟢' : '🔴';
  };

  const getLLMStatusIcon = () => {
    if (isLoading) return '⏳';
    switch (status?.llmStatus) {
      case 'working': return '🟢';
      case 'error': return '🔴';
      default: return '🟡';
    }
  };

  const formatLastChecked = (date: Date) => {
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSeconds = Math.floor(diffMs / 1000);
    const diffMinutes = Math.floor(diffSeconds / 60);

    if (diffSeconds < 60) {
      return `${diffSeconds}s ago`;
    } else if (diffMinutes < 60) {
      return `${diffMinutes}m ago`;
    } else {
      return date.toLocaleTimeString();
    }
  };

  return (
    <div className="server-status">
      <div className="status-header">
        <h3>Server Status</h3>
        <div className="status-controls">
          <label className="auto-refresh-toggle">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh
          </label>
          <button 
            onClick={checkStatus} 
            disabled={isLoading}
            className="refresh-button"
            title="Refresh status"
          >
            {isLoading ? '⏳' : '🔄'}
          </button>
        </div>
      </div>

      <div className="status-items">
        <div className="status-item">
          <div className="status-label">
            <span className="status-icon">{getConnectionStatusIcon()}</span>
            Agent Server
          </div>
          <div className="status-value">
            {isLoading ? (
              'Checking...'
            ) : status?.isConnected ? (
              'Connected'
            ) : (
              <span className="error-text">
                {status?.connectionError || 'Disconnected'}
              </span>
            )}
          </div>
        </div>

        <div className="status-item">
          <div className="status-label">
            <span className="status-icon">{getLLMStatusIcon()}</span>
            LLM Configuration
          </div>
          <div className="status-value">
            {isLoading ? (
              'Testing...'
            ) : status?.llmStatus === 'working' ? (
              'Working'
            ) : status?.llmStatus === 'error' ? (
              <span className="error-text">
                {status?.llmError || 'Error'}
              </span>
            ) : (
              <span className="warning-text">
                {status?.llmError || 'Not configured'}
              </span>
            )}
          </div>
        </div>

        <div className="status-item">
          <div className="status-label">Server URL</div>
          <div className="status-value url-value">
            {settings.agentServerUrl}
          </div>
        </div>

        <div className="status-item">
          <div className="status-label">Model</div>
          <div className="status-value">
            {settings.modelName || 'Not configured'}
          </div>
        </div>

        {status && (
          <div className="status-item">
            <div className="status-label">Last Checked</div>
            <div className="status-value">
              {formatLastChecked(status.lastChecked)}
            </div>
          </div>
        )}
      </div>

      {!status?.isConnected && (
        <div className="status-help">
          <p>💡 <strong>Connection Issues?</strong></p>
          <ul>
            <li>Make sure the agent server is running</li>
            <li>Check that the server URL is correct</li>
            <li>Verify the agent server API key if required</li>
            <li>Check for network connectivity issues</li>
          </ul>
        </div>
      )}

      {status?.isConnected && status?.llmStatus === 'error' && (
        <div className="status-help">
          <p>💡 <strong>LLM Configuration Issues?</strong></p>
          <ul>
            <li>Verify your LLM API key is correct</li>
            <li>Check that the model name is supported</li>
            <li>Ensure you have sufficient API credits</li>
            <li>Check the agent server logs for more details</li>
          </ul>
        </div>
      )}
    </div>
  );
};