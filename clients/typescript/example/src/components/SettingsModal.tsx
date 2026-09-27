import React, { useState, useEffect } from 'react';
import { ServerStatus } from './ServerStatus';

export interface Settings {
  agentServerUrl: string;
  modelName: string;
  apiKey: string;
  agentServerApiKey: string;
}

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (settings: Settings) => void;
  initialSettings?: Settings;
}

const DEFAULT_SETTINGS: Settings = {
  agentServerUrl: 'http://localhost:8000',
  modelName: 'gpt-4',
  apiKey: '',
  agentServerApiKey: ''
};

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  onSave,
  initialSettings = DEFAULT_SETTINGS
}) => {
  const [settings, setSettings] = useState<Settings>(initialSettings);
  const [errors, setErrors] = useState<Partial<Settings>>({});

  useEffect(() => {
    setSettings(initialSettings);
  }, [initialSettings]);

  const validateSettings = (settings: Settings): Partial<Settings> => {
    const errors: Partial<Settings> = {};
    
    if (!settings.agentServerUrl.trim()) {
      errors.agentServerUrl = 'Agent Server URL is required';
    } else if (!isValidUrl(settings.agentServerUrl)) {
      errors.agentServerUrl = 'Please enter a valid URL';
    }
    
    if (!settings.modelName.trim()) {
      errors.modelName = 'Model name is required';
    }
    
    if (!settings.apiKey.trim()) {
      errors.apiKey = 'API key is required';
    }
    
    return errors;
  };

  const isValidUrl = (string: string): boolean => {
    try {
      new URL(string);
      return true;
    } catch (_) {
      return false;
    }
  };

  const handleInputChange = (field: keyof Settings, value: string) => {
    setSettings(prev => ({ ...prev, [field]: value }));
    // Clear error for this field when user starts typing
    if (errors[field]) {
      setErrors(prev => ({ ...prev, [field]: undefined }));
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    const validationErrors = validateSettings(settings);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }
    
    onSave(settings);
    onClose();
  };

  const handleCancel = () => {
    setSettings(initialSettings);
    setErrors({});
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex justify-center items-center z-50" onClick={handleCancel}>
      <div className="bg-white dark:bg-gray-800 rounded-lg p-0 w-[90%] max-w-lg max-h-[90vh] overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center p-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-xl font-semibold text-gray-900 dark:text-white m-0">Settings</h2>
          <button 
            className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white text-2xl border-none bg-transparent cursor-pointer transition-colors duration-200"
            onClick={handleCancel}
          >
            ×
          </button>
        </div>
        
        <div className="p-4 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
          <ServerStatus settings={initialSettings} />
        </div>
        
        <form onSubmit={handleSubmit} className="p-4">
          <div className="mb-4">
            <label htmlFor="agentServerUrl" className="block mb-1 font-semibold text-gray-900 dark:text-white text-sm">
              Agent Server URL
            </label>
            <input
              type="text"
              id="agentServerUrl"
              value={settings.agentServerUrl}
              onChange={(e) => handleInputChange('agentServerUrl', e.target.value)}
              placeholder="http://localhost:8000"
              className={`w-full px-3 py-2 border rounded-md text-sm transition-all duration-200 box-border ${
                errors.agentServerUrl 
                  ? 'border-red-500 shadow-red-100 dark:shadow-red-900/20' 
                  : 'border-gray-300 dark:border-gray-600 focus:border-indigo-600 focus:shadow-indigo-100 dark:focus:shadow-indigo-900/20'
              } bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:shadow-md`}
            />
            {errors.agentServerUrl && (
              <span className="block text-red-500 text-xs mt-1">{errors.agentServerUrl}</span>
            )}
          </div>

          <div className="mb-4">
            <label htmlFor="modelName" className="block mb-1 font-semibold text-gray-900 dark:text-white text-sm">
              Model Name
            </label>
            <input
              type="text"
              id="modelName"
              value={settings.modelName}
              onChange={(e) => handleInputChange('modelName', e.target.value)}
              placeholder="gpt-4"
              className={`w-full px-3 py-2 border rounded-md text-sm transition-all duration-200 box-border ${
                errors.modelName 
                  ? 'border-red-500 shadow-red-100 dark:shadow-red-900/20' 
                  : 'border-gray-300 dark:border-gray-600 focus:border-indigo-600 focus:shadow-indigo-100 dark:focus:shadow-indigo-900/20'
              } bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:shadow-md`}
            />
            {errors.modelName && (
              <span className="block text-red-500 text-xs mt-1">{errors.modelName}</span>
            )}
          </div>

          <div className="mb-4">
            <label htmlFor="apiKey" className="block mb-1 font-semibold text-gray-900 dark:text-white text-sm">
              LLM API Key
            </label>
            <input
              type="password"
              id="apiKey"
              value={settings.apiKey}
              onChange={(e) => handleInputChange('apiKey', e.target.value)}
              placeholder="Enter your LLM API key"
              className={`w-full px-3 py-2 border rounded-md text-sm transition-all duration-200 box-border ${
                errors.apiKey 
                  ? 'border-red-500 shadow-red-100 dark:shadow-red-900/20' 
                  : 'border-gray-300 dark:border-gray-600 focus:border-indigo-600 focus:shadow-indigo-100 dark:focus:shadow-indigo-900/20'
              } bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:shadow-md`}
            />
            {errors.apiKey && (
              <span className="block text-red-500 text-xs mt-1">{errors.apiKey}</span>
            )}
          </div>

          <div className="mb-4">
            <label htmlFor="agentServerApiKey" className="block mb-1 font-semibold text-gray-900 dark:text-white text-sm">
              Agent Server API Key
            </label>
            <input
              type="password"
              id="agentServerApiKey"
              value={settings.agentServerApiKey}
              onChange={(e) => handleInputChange('agentServerApiKey', e.target.value)}
              placeholder="Enter your agent server API key (optional)"
              className={`w-full px-3 py-2 border rounded-md text-sm transition-all duration-200 box-border ${
                errors.agentServerApiKey 
                  ? 'border-red-500 shadow-red-100 dark:shadow-red-900/20' 
                  : 'border-gray-300 dark:border-gray-600 focus:border-indigo-600 focus:shadow-indigo-100 dark:focus:shadow-indigo-900/20'
              } bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:shadow-md`}
            />
            {errors.agentServerApiKey && (
              <span className="block text-red-500 text-xs mt-1">{errors.agentServerApiKey}</span>
            )}
          </div>

          <div className="flex gap-3 justify-end mt-6 pt-3 border-t border-gray-200 dark:border-gray-700">
            <button 
              type="button" 
              onClick={handleCancel} 
              className="px-5 py-2 border border-gray-300 dark:border-gray-600 rounded-md text-sm cursor-pointer transition-all duration-200 bg-gray-50 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-600 hover:text-gray-900 dark:hover:text-white active:translate-y-px"
            >
              Cancel
            </button>
            <button 
              type="submit" 
              className="px-5 py-2 border-none rounded-md text-sm cursor-pointer transition-all duration-200 bg-indigo-600 text-white hover:bg-indigo-700 active:translate-y-px"
            >
              Save Settings
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};