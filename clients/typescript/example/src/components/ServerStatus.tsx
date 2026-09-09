import React, { useState, useEffect } from 'react';
import { Settings } from './SettingsModal';
import { ServerStatus as ServerStatusType, getServerStatus } from '../utils/serverStatus';

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
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 m-0 shadow-sm">
      <div className="flex justify-between items-center mb-3 pb-2 border-b border-gray-200 dark:border-gray-700">
        <h3 className="text-base font-semibold text-gray-900 dark:text-white m-0">Server Status</h3>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="m-0"
            />
            Auto-refresh
          </label>
          <button 
            onClick={checkStatus} 
            disabled={isLoading}
            className="bg-transparent border border-gray-200 dark:border-gray-700 rounded px-2 py-1 cursor-pointer text-base transition-all duration-200 hover:bg-gray-50 dark:hover:bg-gray-700 hover:border-indigo-600 disabled:opacity-60 disabled:cursor-not-allowed text-gray-900 dark:text-white"
            title="Refresh status"
          >
            {isLoading ? '⏳' : '🔄'}
          </button>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex justify-between items-start py-1 border-b border-gray-100 dark:border-gray-700 last:border-b-0">
          <div className="flex items-center gap-2 font-medium text-gray-900 dark:text-white min-w-[120px] text-sm">
            <span className="text-sm w-4 text-center">{getConnectionStatusIcon()}</span>
            Agent Server
          </div>
          <div className="text-right text-gray-600 dark:text-gray-400 max-w-[60%] break-words text-sm">
            {isLoading ? (
              'Checking...'
            ) : status?.isConnected ? (
              'Connected'
            ) : (
              <span className="text-red-600 dark:text-red-400 font-medium">
                {status?.connectionError || 'Disconnected'}
              </span>
            )}
          </div>
        </div>

        <div className="flex justify-between items-start py-1 border-b border-gray-100 dark:border-gray-700 last:border-b-0">
          <div className="flex items-center gap-2 font-medium text-gray-900 dark:text-white min-w-[120px] text-sm">
            <span className="text-sm w-4 text-center">{getLLMStatusIcon()}</span>
            LLM Configuration
          </div>
          <div className="text-right text-gray-600 dark:text-gray-400 max-w-[60%] break-words text-sm">
            {isLoading ? (
              'Testing...'
            ) : status?.llmStatus === 'working' ? (
              'Working'
            ) : status?.llmStatus === 'error' ? (
              <span className="text-red-600 dark:text-red-400 font-medium">
                {status?.llmError || 'Error'}
              </span>
            ) : (
              <span className="text-orange-600 dark:text-orange-400 font-medium">
                {status?.llmError || 'Not configured'}
              </span>
            )}
          </div>
        </div>

        <div className="flex justify-between items-start py-1 border-b border-gray-100 dark:border-gray-700 last:border-b-0">
          <div className="flex items-center gap-2 font-medium text-gray-900 dark:text-white min-w-[120px] text-sm">
            Server URL
          </div>
          <div className="text-right text-gray-600 dark:text-gray-400 max-w-[60%] break-words text-sm font-mono text-xs bg-gray-50 dark:bg-gray-700 px-2 py-1 rounded border border-gray-100 dark:border-gray-600">
            {settings.agentServerUrl}
          </div>
        </div>

        <div className="flex justify-between items-start py-1 border-b border-gray-100 dark:border-gray-700 last:border-b-0">
          <div className="flex items-center gap-2 font-medium text-gray-900 dark:text-white min-w-[120px] text-sm">
            Model
          </div>
          <div className="text-right text-gray-600 dark:text-gray-400 max-w-[60%] break-words text-sm">
            {settings.modelName || 'Not configured'}
          </div>
        </div>

        {status && (
          <div className="flex justify-between items-start py-1 border-b border-gray-100 dark:border-gray-700 last:border-b-0">
            <div className="flex items-center gap-2 font-medium text-gray-900 dark:text-white min-w-[120px] text-sm">
              Last Checked
            </div>
            <div className="text-right text-gray-600 dark:text-gray-400 max-w-[60%] break-words text-sm">
              {formatLastChecked(status.lastChecked)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};