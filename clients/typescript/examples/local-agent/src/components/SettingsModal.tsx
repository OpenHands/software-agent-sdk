import { useState } from 'react';
import type { ChatConfig } from '../App';

interface SettingsModalProps {
  config: ChatConfig;
  models: { id: string; name: string }[];
  onClose: () => void;
  onSave: (config: Partial<ChatConfig>) => void;
}

export function SettingsModal({ config, models, onClose, onSave }: SettingsModalProps) {
  const [model, setModel] = useState(config.model);
  const [temperature, setTemperature] = useState(config.temperature);
  const [maxTokens, setMaxTokens] = useState(config.maxTokens);

  const handleSave = () => {
    onSave({ model, temperature, maxTokens });
  };

  return (
    <div id="settings-modal-overlay" className="modal-overlay" onClick={onClose}>
      <div id="settings-modal" className="modal" onClick={(e) => e.stopPropagation()}>
        <h3 id="settings-modal-title">Settings</h3>

        <div className="form-group">
          <label htmlFor="settings-model-select">Model</label>
          <select
            id="settings-model-select"
            className="model-select"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            style={{ width: '100%' }}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </div>

        <div className="form-group" style={{ marginTop: '1rem' }}>
          <label id="settings-temperature-label" htmlFor="settings-temperature-input">
            Temperature: {temperature.toFixed(1)}
          </label>
          <input
            id="settings-temperature-input"
            type="range"
            min="0"
            max="2"
            step="0.1"
            value={temperature}
            onChange={(e) => setTemperature(parseFloat(e.target.value))}
            style={{ width: '100%' }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
            <span>Precise</span>
            <span>Creative</span>
          </div>
        </div>

        <div className="form-group" style={{ marginTop: '1rem' }}>
          <label id="settings-tokens-label" htmlFor="settings-tokens-input">Max Tokens: {maxTokens}</label>
          <input
            id="settings-tokens-input"
            type="range"
            min="256"
            max="8192"
            step="256"
            value={maxTokens}
            onChange={(e) => setMaxTokens(parseInt(e.target.value))}
            style={{ width: '100%' }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
            <span>256</span>
            <span>8192</span>
          </div>
        </div>

        <div id="settings-modal-actions" className="modal-actions">
          <button id="settings-cancel-button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button id="settings-save-button" className="btn btn-primary" onClick={handleSave}>
            Save Changes
          </button>
        </div>
      </div>
    </div>
  );
}
