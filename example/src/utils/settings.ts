import { Settings } from '../components/SettingsModal';

const SETTINGS_KEY = 'openhands-settings';
const FIRST_VISIT_KEY = 'openhands-first-visit';

export const DEFAULT_SETTINGS: Settings = {
  agentServerUrl: 'http://localhost:8000',
  modelName: 'gpt-4',
  apiKey: '',
  agentServerApiKey: ''
};

/**
 * Load settings from localStorage
 */
export const loadSettings = (): Settings => {
  try {
    const stored = localStorage.getItem(SETTINGS_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      // Merge with defaults to ensure all required fields are present
      return { ...DEFAULT_SETTINGS, ...parsed };
    }
  } catch (error) {
    console.warn('Failed to load settings from localStorage:', error);
  }
  return DEFAULT_SETTINGS;
};

/**
 * Save settings to localStorage
 */
export const saveSettings = (settings: Settings): void => {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch (error) {
    console.error('Failed to save settings to localStorage:', error);
  }
};

/**
 * Check if this is the user's first visit
 */
export const isFirstVisit = (): boolean => {
  try {
    return !localStorage.getItem(FIRST_VISIT_KEY);
  } catch (error) {
    console.warn('Failed to check first visit status:', error);
    return true; // Default to first visit if we can't check
  }
};

/**
 * Mark that the user has visited the app
 */
export const markVisited = (): void => {
  try {
    localStorage.setItem(FIRST_VISIT_KEY, 'true');
  } catch (error) {
    console.error('Failed to mark app as visited:', error);
  }
};

/**
 * Check if settings are configured (have non-default values)
 */
export const areSettingsConfigured = (settings: Settings): boolean => {
  return (
    settings.agentServerUrl !== DEFAULT_SETTINGS.agentServerUrl ||
    settings.modelName !== DEFAULT_SETTINGS.modelName ||
    settings.apiKey !== DEFAULT_SETTINGS.apiKey ||
    settings.agentServerApiKey !== DEFAULT_SETTINGS.agentServerApiKey
  );
};

/**
 * Clear all stored settings and first visit flag
 */
export const clearAllSettings = (): void => {
  try {
    localStorage.removeItem(SETTINGS_KEY);
    localStorage.removeItem(FIRST_VISIT_KEY);
  } catch (error) {
    console.error('Failed to clear settings:', error);
  }
};