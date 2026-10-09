// Import settings components
import { SettingsModal } from './components/SettingsModal'
import { ConversationManager } from './components/ConversationManager'
import { useSettings } from './contexts/SettingsContext'

function App() {
  // Use settings context
  const { settings, updateSettings, isModalOpen, openModal, closeModal, isFirstVisit } = useSettings()

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100">
      <div className="max-w-7xl mx-auto p-8 text-center">
        <div className="flex justify-between items-center mb-8">
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white m-0">OpenHands Conversation Manager</h1>
          <button 
            className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-3 rounded-md font-medium transition-colors duration-200"
            onClick={openModal}
          >
            ⚙️ Settings
          </button>
        </div>
        
        {isFirstVisit && (
          <div className="bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-700 rounded-md p-4 mb-4 text-center">
            <p className="text-blue-700 dark:text-blue-300 font-medium m-0">👋 Welcome! Please configure your settings to get started.</p>
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