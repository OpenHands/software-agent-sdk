import { useState } from 'react';

interface AuthScreenProps {
  onAuthenticate: (apiKey: string, model: string) => void;
  models: { id: string; name: string }[];
  defaultModel: string;
}

export function AuthScreen({ onAuthenticate, models, defaultModel }: AuthScreenProps) {
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState(defaultModel);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);

    try {
      // Validate API key by making a test request
      const response = await fetch('https://openrouter.ai/api/v1/auth/key', {
        headers: {
          'Authorization': `Bearer ${apiKey}`,
        },
      });

      if (!response.ok) {
        throw new Error('Invalid API key. Please check and try again.');
      }

      onAuthenticate(apiKey, model);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication failed');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div id="auth-app-container" className="app">
      <header id="auth-header" className="header">
        <h1 id="auth-title">
          <span className="logo">🤖</span>
          OpenHands Chat
        </h1>
      </header>

      <div id="auth-screen" className="auth-screen">
        <div id="auth-card" className="auth-card">
          <h2 id="auth-welcome-title">Welcome</h2>
          <p id="auth-welcome-text">
            Connect your OpenRouter API key to start chatting with AI models.
          </p>

          <form id="auth-form" className="auth-form" onSubmit={handleSubmit}>
            {error && <div id="auth-error-message" className="error-message">{error}</div>}

            <div className="form-group">
              <label htmlFor="auth-api-key-input">OpenRouter API Key</label>
              <input
                id="auth-api-key-input"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-or-v1-..."
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="auth-model-select">Default Model</label>
              <select
                id="auth-model-select"
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

            <button
              id="auth-connect-button"
              type="submit"
              className="btn btn-primary"
              disabled={!apiKey || isLoading}
              style={{ width: '100%' }}
            >
              {isLoading ? 'Connecting...' : 'Connect'}
            </button>
          </form>

          <div id="auth-link-container" className="auth-link">
            Don't have an API key?{' '}
            <a id="auth-openrouter-link" href="https://openrouter.ai/keys" target="_blank" rel="noopener noreferrer">
              Get one at OpenRouter →
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
