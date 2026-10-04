import { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { LocalConversation, LocalWorkspace, Agent } from '@openhands/typescript-client';
import type { LLM, Tool, ToolCall } from '@openhands/typescript-client';

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  timestamp: Date;
  toolCalls?: ToolCall[];
  toolCallId?: string;
  toolName?: string;
}

interface AgentChatInterfaceProps {
  llm: LLM;
  model: string;
}

// Define the tools including UI interaction tools
const TOOLS: Tool[] = [
  {
    type: 'function',
    function: {
      name: 'ui_interact',
      description: `Retrieves a DOM element by its ID and executes JavaScript code with the element available as 'el'. 
Use this to interact with UI elements: click buttons, change styles, read/set values, etc.
The element is retrieved using document.getElementById() and made available as 'el' in your code.
Returns the result of the code execution or an error message if the element is not found.`,
      parameters: {
        type: 'object',
        properties: {
          elementId: {
            type: 'string',
            description: 'The ID of the DOM element to retrieve (e.g., "settings-button", "chat-input")',
          },
          code: {
            type: 'string',
            description: 'JavaScript code to execute with the element available as "el". Examples: "el.click()", "el.style.backgroundColor = \'blue\'", "el.value", "el.textContent"',
          },
        },
        required: ['elementId', 'code'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'ui_list_elements',
      description: `Lists all interactive elements on the page with their IDs, tag names, and text content.
Use this to discover what elements are available for interaction.
Returns a JSON array of element information.`,
      parameters: {
        type: 'object',
        properties: {
          selector: {
            type: 'string',
            description: 'Optional CSS selector to filter elements (default: "[id]" for all elements with IDs). Examples: "button[id]", "input[id]", "[id^=settings]"',
          },
        },
        required: [],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'eval',
      description: 'Evaluates arbitrary JavaScript code in the browser and returns the result. Use this for general calculations, data manipulation, or operations that don\'t target a specific element.',
      parameters: {
        type: 'object',
        properties: {
          code: {
            type: 'string',
            description: 'The JavaScript code to evaluate',
          },
        },
        required: ['code'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'finish',
      description: 'Call this when you have completed the task and want to end the conversation.',
      parameters: {
        type: 'object',
        properties: {
          message: {
            type: 'string',
            description: 'Final message or summary to present to the user',
          },
        },
        required: ['message'],
      },
    },
  },
];

const SYSTEM_PROMPT = `You are a helpful AI assistant that can interact with UI elements on this web page.

Available tools:

1. **ui_interact** - Interact with a specific UI element by ID
   - Retrieves an element by ID and executes code with it available as 'el'
   - Use for: clicking buttons, changing styles, reading/setting values
   - Example: elementId="settings-button", code="el.click()"
   - Example: elementId="settings-button", code="el.style.backgroundColor = 'blue'"

2. **ui_list_elements** - Discover available elements on the page
   - Lists all elements with IDs (or filtered by selector)
   - Use this first to see what elements are available
   - Returns: array of {id, tagName, type, text, value}

3. **eval** - Execute arbitrary JavaScript code
   - For general calculations or operations not targeting a specific element
   - Example: "2 + 2" or "Array.from({length: 10}, (_, i) => i * i)"

4. **finish** - Complete the task
   - Call when you've finished the user's request

## Available UI Element IDs:
- **Header**: app-container, app-header, app-title, header-controls
- **Controls**: model-selector, status-badge, settings-button, logout-button
- **Chat**: chat-container, chat-messages, chat-input-area, chat-input, chat-send-button, chat-clear-button
- **Settings Modal** (when open): settings-modal-overlay, settings-modal, settings-model-select, settings-temperature-input, settings-tokens-input, settings-cancel-button, settings-save-button

## Workflow:
1. If unsure what elements exist, use ui_list_elements first
2. Use ui_interact to manipulate specific elements
3. Call finish when done

Remember: When using ui_interact, the element is available as 'el' in your code.`;

export function AgentChatInterface({ llm, model }: AgentChatInterfaceProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const conversationRef = useRef<LocalConversation | null>(null);
  const pendingMessagesRef = useRef<Message[]>([]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, [input]);

  // Tool executor function that will be passed to LocalConversation
  const toolExecutor = useCallback((toolCall: ToolCall): string => {
    const { name, arguments: argsString } = toolCall.function;
    
    try {
      const args = JSON.parse(argsString);
      
      if (name === 'ui_interact') {
        const { elementId, code } = args;
        console.log('[Agent Tool] ui_interact:', elementId, code);
        try {
          const el = document.getElementById(elementId);
          if (!el) {
            return `Error: Element with ID "${elementId}" not found. Use ui_list_elements to see available elements.`;
          }
          // Execute code with element available as 'el'
          // eslint-disable-next-line no-eval
          const result = eval(`(function(el) { return ${code}; })`)(el);
          const resultStr = typeof result === 'undefined' ? 'undefined' : 
                           result instanceof Element ? `<${result.tagName.toLowerCase()} id="${result.id || '(no id)'}">` :
                           JSON.stringify(result, null, 2);
          console.log('[Agent Tool] ui_interact result:', result);
          return resultStr;
        } catch (evalError) {
          const errorMsg = evalError instanceof Error ? evalError.message : String(evalError);
          console.error('[Agent Tool] ui_interact error:', errorMsg);
          return `Error: ${errorMsg}`;
        }
      }
      
      if (name === 'ui_list_elements') {
        const selector = args.selector || '[id]';
        console.log('[Agent Tool] ui_list_elements:', selector);
        try {
          const elements = document.querySelectorAll(selector);
          const elementList = Array.from(elements).map((el) => {
            const htmlEl = el as HTMLElement;
            const inputEl = el as HTMLInputElement;
            return {
              id: htmlEl.id || '(no id)',
              tagName: htmlEl.tagName.toLowerCase(),
              type: inputEl.type || undefined,
              text: (htmlEl.textContent || '').trim().slice(0, 50) || undefined,
              value: inputEl.value || undefined,
              className: htmlEl.className || undefined,
            };
          }).filter(e => e.id !== '(no id)'); // Only return elements with IDs
          console.log('[Agent Tool] ui_list_elements result:', elementList);
          return JSON.stringify(elementList, null, 2);
        } catch (error) {
          const errorMsg = error instanceof Error ? error.message : String(error);
          return `Error: ${errorMsg}`;
        }
      }
      
      if (name === 'eval') {
        const code = args.code || '';
        console.log('[Agent Tool] Evaluating:', code);
        try {
          // eslint-disable-next-line no-eval
          const result = eval(code);
          const resultStr = typeof result === 'undefined' ? 'undefined' : JSON.stringify(result, null, 2);
          console.log('[Agent Tool] Result:', result);
          return resultStr;
        } catch (evalError) {
          const errorMsg = evalError instanceof Error ? evalError.message : String(evalError);
          console.error('[Agent Tool] Eval error:', errorMsg);
          return `Error: ${errorMsg}`;
        }
      }
      
      if (name === 'finish') {
        return `Task completed: ${args.message || ''}`;
      }
      
      return `Unknown tool: ${name}`;
    } catch (error) {
      return `Error executing tool: ${error instanceof Error ? error.message : 'Unknown error'}`;
    }
  }, []);

  // Event callback for the conversation
  const handleConversationEvent = useCallback((event: unknown) => {
    const eventData = event as Record<string, unknown>;
    
    if (eventData.kind === 'assistant_message' && eventData.content) {
      pendingMessagesRef.current.push({
        id: `${Date.now()}-assistant-${Math.random()}`,
        role: 'assistant',
        content: eventData.content as string,
        timestamp: new Date(),
      });
    } else if (eventData.kind === 'tool_call') {
      // Show the tool call with its inputs
      pendingMessagesRef.current.push({
        id: `${Date.now()}-toolcall-${Math.random()}`,
        role: 'tool',
        content: eventData.arguments as string,
        timestamp: new Date(),
        toolName: eventData.tool as string,
        toolCallId: 'call',
      });
    } else if (eventData.kind === 'tool_result') {
      // Show the tool result
      pendingMessagesRef.current.push({
        id: `${Date.now()}-toolresult-${Math.random()}`,
        role: 'tool',
        content: eventData.result as string,
        timestamp: new Date(),
        toolName: eventData.tool as string,
      });
    } else if (eventData.kind === 'finish') {
      pendingMessagesRef.current.push({
        id: `${Date.now()}-finish-${Math.random()}`,
        role: 'assistant',
        content: eventData.message as string,
        timestamp: new Date(),
      });
    }
  }, []);

  const sendMessage = async () => {
    if (!input.trim() || isLoading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input.trim(),
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);

    // Clear pending messages for this run
    pendingMessagesRef.current = [];

    try {
      // Create conversation if it doesn't exist
      if (!conversationRef.current) {
        const workspace = new LocalWorkspace({ workingDir: '/workspace' });
        const agent = new Agent({
          llm: { model, api_key: '' },
        });
        
        conversationRef.current = new LocalConversation(agent, workspace, {
          llm,
          systemPrompt: SYSTEM_PROMPT,
          tools: TOOLS,
          toolExecutor,
          maxIterations: 10,
          callback: handleConversationEvent,
        });
        
        // Start the conversation with the first message
        await conversationRef.current.start({ initialMessage: userMessage.content });
      } else {
        // Send follow-up message to existing conversation
        await conversationRef.current.sendMessage(userMessage.content);
      }
      
      // Run the agent loop
      await conversationRef.current.run();
      
      // Update messages with collected events
      setMessages((prev) => [...prev, ...pendingMessagesRef.current]);
      
    } catch (error) {
      console.error('Error in agent loop:', error);

      const errorMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: `Error: ${error instanceof Error ? error.message : 'Failed to get response'}`,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const clearChat = () => {
    setMessages([]);
    conversationRef.current = null; // Reset conversation to start fresh
  };

  return (
    <div id="chat-container" className="chat-container">
      {messages.length === 0 ? (
        <div id="chat-empty-state" className="empty-state">
          <div className="icon">🤖</div>
          <h3 id="chat-empty-title">Agent Ready</h3>
          <p id="chat-empty-description">
            This agent can interact with UI elements! Try asking it to click buttons,
            change colors, or manipulate elements by their IDs.
          </p>
          <p id="chat-empty-examples" style={{ marginTop: '1rem', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
            Examples: "Turn the settings button blue" or "Click the logout button" or "List all buttons on the page"
          </p>
        </div>
      ) : (
        <div id="chat-messages" className="messages">
          {messages.map((message, index) => (
            <div key={message.id} id={`chat-message-${index}`} className={`message ${message.role}`}>
              <div className="message-avatar">
                {message.role === 'user' ? '👤' : message.role === 'tool' ? '🔧' : '🤖'}
              </div>
              <div className="message-content">
                {message.role === 'tool' ? (
                  <div className="tool-result">
                    <div className="tool-header">
                      <span className="tool-icon">{message.toolCallId ? '📤' : '📥'}</span>
                      <span className="tool-name">{message.toolName}</span>
                      <span className="tool-type" style={{ marginLeft: '0.5rem', fontSize: '0.75rem', opacity: 0.7 }}>
                        {message.toolCallId ? '(call)' : '(result)'}
                      </span>
                    </div>
                    <pre className="tool-output">{message.content}</pre>
                  </div>
                ) : (
                  <>
                    <ReactMarkdown>{message.content}</ReactMarkdown>
                    {message.toolCalls && message.toolCalls.length > 0 && (
                      <div className="tool-calls">
                        {message.toolCalls.map((tc) => (
                          <div key={tc.id} className="tool-call">
                            <span className="tool-icon">🔧</span>
                            <span className="tool-name">{tc.function.name}</span>
                            <code className="tool-args">{tc.function.arguments}</code>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
          {isLoading && (
            <div id="chat-loading-indicator" className="message assistant">
              <div className="message-avatar">🤖</div>
              <div className="typing-indicator">
                <div className="typing-dots">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
                Thinking...
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      )}

      <div id="chat-input-area" className="input-area">
        <div id="chat-input-container" className="input-container">
          <textarea
            id="chat-input"
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask the agent to do something... (Shift+Enter for new line)"
            disabled={isLoading}
            rows={1}
          />
          <button
            id="chat-send-button"
            className="send-btn"
            onClick={sendMessage}
            disabled={!input.trim() || isLoading}
            title="Send message"
          >
            ➤
          </button>
        </div>
        {messages.length > 0 && (
          <div style={{ marginTop: '0.5rem', textAlign: 'center' }}>
            <button id="chat-clear-button" className="btn btn-secondary" onClick={clearChat} style={{ fontSize: '0.75rem' }}>
              Clear conversation
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
