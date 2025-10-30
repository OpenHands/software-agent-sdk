import './App.css'

// Import settings components
import { SettingsModal } from './components/SettingsModal'
import { ConversationManager } from './components/ConversationManager'
import { useSettings } from './contexts/SettingsContext'

function App() {
  // Use settings context
  const { settings, updateSettings, isModalOpen, openModal, closeModal, isFirstVisit } = useSettings()

  return (
    <div className="App">
      <div>
        <div className="app-header">
          <h1>OpenHands Conversation Manager</h1>
          <button className="settings-button" onClick={openModal}>
            ⚙️ Settings
          </button>
        </div>
        
        {isFirstVisit && (
          <div className="welcome-message">
            <p>👋 Welcome! Please configure your settings to get started.</p>
          </div>
        )}
        
        <ConversationManager />
        
        <SettingsModal
          isOpen={isModalOpen}
          onClose={closeModal}
          onSave={updateSettings}
          initialSettings={settings}
        />
      </div>
    </div>
  )
}

export default App