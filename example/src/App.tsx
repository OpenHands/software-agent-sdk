import { useState, useEffect } from 'react'
import './App.css'

// Import the OpenHands SDK
import { 
  RemoteConversation, 
  RemoteWorkspace, 
  HttpClient, 
  AgentExecutionStatus,
  EventSortOrder 
} from '@openhands/agent-server-typescript-client'

function App() {
  const [sdkStatus, setSdkStatus] = useState<string>('Loading...')
  const [sdkInfo, setSdkInfo] = useState<any>(null)

  useEffect(() => {
    // Test that the SDK imports work correctly
    try {
      // Check that all main classes are available
      const classes = {
        RemoteConversation: typeof RemoteConversation,
        RemoteWorkspace: typeof RemoteWorkspace,
        HttpClient: typeof HttpClient,
        AgentExecutionStatus: typeof AgentExecutionStatus,
        EventSortOrder: typeof EventSortOrder,
      }

      // Check that enums have expected values
      const enumValues = {
        AgentExecutionStatus: Object.keys(AgentExecutionStatus),
        EventSortOrder: Object.keys(EventSortOrder),
      }

      setSdkInfo({
        classes,
        enumValues,
        importTime: new Date().toISOString(),
      })

      setSdkStatus('✅ SDK imported successfully!')
    } catch (error) {
      setSdkStatus(`❌ SDK import failed: ${error}`)
      console.error('SDK import error:', error)
    }
  }, [])

  return (
    <div className="App">
      <div>
        <h1>OpenHands SDK Example</h1>
        <div className="card">
          <h2>SDK Import Status</h2>
          <p className="status">{sdkStatus}</p>
          
          {sdkInfo && (
            <div className="sdk-info">
              <h3>Available Classes:</h3>
              <ul>
                {Object.entries(sdkInfo.classes).map(([name, type]) => (
                  <li key={name}>
                    <strong>{name}</strong>: {type as string}
                  </li>
                ))}
              </ul>
              
              <h3>Enum Values:</h3>
              <div className="enums">
                <div>
                  <strong>AgentExecutionStatus:</strong>
                  <ul>
                    {sdkInfo.enumValues.AgentExecutionStatus.map((value: string) => (
                      <li key={value}>{value}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <strong>EventSortOrder:</strong>
                  <ul>
                    {sdkInfo.enumValues.EventSortOrder.map((value: string) => (
                      <li key={value}>{value}</li>
                    ))}
                  </ul>
                </div>
              </div>
              
              <p className="import-time">
                <small>Imported at: {sdkInfo.importTime}</small>
              </p>
            </div>
          )}
        </div>
        
        <div className="card">
          <h2>Hello World from React + TypeScript + OpenHands SDK!</h2>
          <p>
            This is a basic React application that successfully imports and uses the 
            OpenHands Agent Server TypeScript Client SDK.
          </p>
          <p>
            The SDK is built locally and linked as a file dependency, demonstrating 
            that the build process works correctly.
          </p>
        </div>
      </div>
    </div>
  )
}

export default App