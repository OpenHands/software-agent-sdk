import React, { useState, useEffect } from 'react';
import './SettingsModal.css';
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
    <div className="modal-overlay" onClick={handleCancel}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Settings</h2>
          <button className="close-button" onClick={handleCancel}>
            ×
          </button>
        </div>
        
        <div className="settings-status">
          <ServerStatus settings={initialSettings} />
        </div>
        
        <form onSubmit={handleSubmit} className="settings-form">
          <div className="form-group">
            <label htmlFor="agentServerUrl">Agent Server URL</label>
            <input
              type="text"
              id="agentServerUrl"
              value={settings.agentServerUrl}
              onChange={(e) => handleInputChange('agentServerUrl', e.target.value)}
              placeholder="http://localhost:8000"
              className={errors.agentServerUrl ? 'error' : ''}
            />
            {errors.agentServerUrl && (
              <span className="error-message">{errors.agentServerUrl}</span>
            )}
          </div>

          <div className="form-group">
            <label htmlFor="modelName">Model Name</label>
            <input
              type="text"
              id="modelName"
              value={settings.modelName}
              onChange={(e) => handleInputChange('modelName', e.target.value)}
              placeholder="gpt-4"
              className={errors.modelName ? 'error' : ''}
            />
            {errors.modelName && (
              <span className="error-message">{errors.modelName}</span>
            )}
          </div>

          <div className="form-group">
            <label htmlFor="apiKey">LLM API Key</label>
            <input
              type="password"
              id="apiKey"
              value={settings.apiKey}
              onChange={(e) => handleInputChange('apiKey', e.target.value)}
              placeholder="Enter your LLM API key"
              className={errors.apiKey ? 'error' : ''}
            />
            {errors.apiKey && (
              <span className="error-message">{errors.apiKey}</span>
            )}
          </div>

          <div className="form-group">
            <label htmlFor="agentServerApiKey">Agent Server API Key</label>
            <input
              type="password"
              id="agentServerApiKey"
              value={settings.agentServerApiKey}
              onChange={(e) => handleInputChange('agentServerApiKey', e.target.value)}
              placeholder="Enter your agent server API key (optional)"
              className={errors.agentServerApiKey ? 'error' : ''}
            />
            {errors.agentServerApiKey && (
              <span className="error-message">{errors.agentServerApiKey}</span>
            )}
          </div>

          <div className="form-actions">
            <button type="button" onClick={handleCancel} className="cancel-button">
              Cancel
            </button>
            <button type="submit" className="save-button">
              Save Settings
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};