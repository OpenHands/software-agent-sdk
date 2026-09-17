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
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Settings</h3>

        <div className="form-group">
          <label htmlFor="settings-model">Model</label>
          <select
            id="settings-model"
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
          <label htmlFor="settings-temperature">
            Temperature: {temperature.toFixed(1)}
          </label>
          <input
            id="settings-temperature"
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
          <label htmlFor="settings-tokens">Max Tokens: {maxTokens}</label>
          <input
            id="settings-tokens"
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

        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={handleSave}>
            Save Changes
          </button>
        </div>
      </div>
    </div>
  );
}
