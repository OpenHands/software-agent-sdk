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
    <div className="app">
      <header className="header">
        <h1>
          <span className="logo">🤖</span>
          OpenHands Chat
        </h1>
      </header>

      <div className="auth-screen">
        <div className="auth-card">
          <h2>Welcome</h2>
          <p>
            Connect your OpenRouter API key to start chatting with AI models.
          </p>

          <form className="auth-form" onSubmit={handleSubmit}>
            {error && <div className="error-message">{error}</div>}

            <div className="form-group">
              <label htmlFor="apiKey">OpenRouter API Key</label>
              <input
                id="apiKey"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-or-v1-..."
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="model">Default Model</label>
              <select
                id="model"
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
              type="submit"
              className="btn btn-primary"
              disabled={!apiKey || isLoading}
              style={{ width: '100%' }}
            >
              {isLoading ? 'Connecting...' : 'Connect'}
            </button>
          </form>

          <div className="auth-link">
            Don't have an API key?{' '}
            <a href="https://openrouter.ai/keys" target="_blank" rel="noopener noreferrer">
              Get one at OpenRouter →
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
