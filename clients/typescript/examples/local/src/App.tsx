import { useState, useEffect } from 'react';
import { LLM } from '@openhands/typescript-client';
import { AuthScreen } from './components/AuthScreen';
import { ChatInterface } from './components/ChatInterface';
import { SettingsModal } from './components/SettingsModal';

export interface ChatConfig {
  apiKey: string;
  model: string;
  temperature: number;
  maxTokens: number;
}

const DEFAULT_MODEL = 'anthropic/claude-3.5-sonnet';

const POPULAR_MODELS = [
  { id: 'anthropic/claude-3.5-sonnet', name: 'Claude 3.5 Sonnet' },
  { id: 'anthropic/claude-3-haiku', name: 'Claude 3 Haiku' },
  { id: 'openai/gpt-4o', name: 'GPT-4o' },
  { id: 'openai/gpt-4o-mini', name: 'GPT-4o Mini' },
  { id: 'google/gemini-pro-1.5', name: 'Gemini Pro 1.5' },
  { id: 'meta-llama/llama-3.1-70b-instruct', name: 'Llama 3.1 70B' },
  { id: 'mistralai/mistral-large', name: 'Mistral Large' },
];

function App() {
  const [config, setConfig] = useState<ChatConfig | null>(null);
  const [llm, setLlm] = useState<LLM | null>(null);
  const [showSettings, setShowSettings] = useState(false);

  // Load saved config from localStorage
  useEffect(() => {
    const savedApiKey = localStorage.getItem('openrouter_api_key');
    const savedModel = localStorage.getItem('openrouter_model') || DEFAULT_MODEL;
    
    if (savedApiKey) {
      const loadedConfig: ChatConfig = {
        apiKey: savedApiKey,
        model: savedModel,
        temperature: 0.7,
        maxTokens: 4096,
      };
      setConfig(loadedConfig);
    }
  }, []);

  // Create LLM instance when config changes
  useEffect(() => {
    if (config) {
      const newLlm = new LLM({
        apiKey: config.apiKey,
        defaultModel: config.model,
        defaultTemperature: config.temperature,
        defaultMaxTokens: config.maxTokens,
      });
      setLlm(newLlm);
    } else {
      setLlm(null);
    }
  }, [config]);

  const handleAuthenticate = (apiKey: string, model: string) => {
    localStorage.setItem('openrouter_api_key', apiKey);
    localStorage.setItem('openrouter_model', model);
    
    setConfig({
      apiKey,
      model,
      temperature: 0.7,
      maxTokens: 4096,
    });
  };

  const handleLogout = () => {
    localStorage.removeItem('openrouter_api_key');
    setConfig(null);
    setLlm(null);
  };

  const handleModelChange = (model: string) => {
    if (config) {
      localStorage.setItem('openrouter_model', model);
      setConfig({ ...config, model });
    }
  };

  const handleSettingsUpdate = (newConfig: Partial<ChatConfig>) => {
    if (config) {
      const updated = { ...config, ...newConfig };
      setConfig(updated);
      if (newConfig.model) {
        localStorage.setItem('openrouter_model', newConfig.model);
      }
    }
    setShowSettings(false);
  };

  if (!config || !llm) {
    return (
      <AuthScreen
        onAuthenticate={handleAuthenticate}
        models={POPULAR_MODELS}
        defaultModel={DEFAULT_MODEL}
      />
    );
  }

  return (
    <div className="app">
      <header className="header">
        <h1>
          <span className="logo">🤖</span>
          OpenHands Chat
        </h1>
        <div className="header-right">
          <select
            className="model-select"
            value={config.model}
            onChange={(e) => handleModelChange(e.target.value)}
          >
            {POPULAR_MODELS.map((model) => (
              <option key={model.id} value={model.id}>
                {model.name}
              </option>
            ))}
          </select>
          <div className="status-badge connected">
            <span className="status-dot"></span>
            Connected
          </div>
          <button className="btn btn-secondary" onClick={() => setShowSettings(true)}>
            ⚙️
          </button>
          <button className="btn btn-danger" onClick={handleLogout}>
            Logout
          </button>
        </div>
      </header>

      <ChatInterface llm={llm} model={config.model} />

      {showSettings && (
        <SettingsModal
          config={config}
          models={POPULAR_MODELS}
          onClose={() => setShowSettings(false)}
          onSave={handleSettingsUpdate}
        />
      )}
    </div>
  );
}

export default App;
