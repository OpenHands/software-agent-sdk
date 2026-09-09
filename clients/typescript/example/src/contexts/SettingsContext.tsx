import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { Settings } from '../components/SettingsModal';
import { loadSettings, saveSettings, isFirstVisit, markVisited } from '../utils/settings';

interface SettingsContextType {
  settings: Settings;
  updateSettings: (newSettings: Settings) => void;
  isModalOpen: boolean;
  openModal: () => void;
  closeModal: () => void;
  isFirstVisit: boolean;
}

const SettingsContext = createContext<SettingsContextType | undefined>(undefined);

interface SettingsProviderProps {
  children: ReactNode;
}

export const SettingsProvider: React.FC<SettingsProviderProps> = ({ children }) => {
  const [settings, setSettings] = useState<Settings>(loadSettings());
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [firstVisit, setFirstVisit] = useState(true);

  useEffect(() => {
    // Check if this is the first visit
    const isFirst = isFirstVisit();
    setFirstVisit(isFirst);
    
    if (isFirst) {
      // Open modal on first visit
      setIsModalOpen(true);
      // Mark as visited
      markVisited();
    }
  }, []);

  const updateSettings = (newSettings: Settings) => {
    setSettings(newSettings);
    saveSettings(newSettings);
  };

  const openModal = () => {
    setIsModalOpen(true);
  };

  const closeModal = () => {
    setIsModalOpen(false);
  };

  const value: SettingsContextType = {
    settings,
    updateSettings,
    isModalOpen,
    openModal,
    closeModal,
    isFirstVisit: firstVisit,
  };

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  );
};

export const useSettings = (): SettingsContextType => {
  const context = useContext(SettingsContext);
  if (context === undefined) {
    throw new Error('useSettings must be used within a SettingsProvider');
  }
  return context;
};