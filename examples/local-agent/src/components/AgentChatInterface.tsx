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

// Define the eval tool
const TOOLS: Tool[] = [
  {
    type: 'function',
    function: {
      name: 'eval',
      description: 'Evaluates JavaScript code in the browser and returns the result. Use this to perform calculations, manipulate data, or execute any JavaScript code.',
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

const SYSTEM_PROMPT = `You are a helpful AI assistant with access to JavaScript evaluation capabilities.

Available tools:
- eval: Evaluates JavaScript code in the browser and returns the result. Use this for calculations, data manipulation, or any JavaScript operations.
- finish: Call this when you have completed the task to end the conversation.

When the user asks you to do something, use the eval tool to execute JavaScript code. After completing the task, call finish with a summary of what you did.`;

export function AgentChatInterface({ llm, model }: AgentChatInterfaceProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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

    try {
      // Create a minimal workspace (won't be used since we have custom tools)
      const workspace = new LocalWorkspace({ workingDir: '/workspace' });
      
      // Create an agent configuration
      const agent = new Agent({
        llm: { model, api_key: '' }, // LLM config (actual LLM instance passed separately)
      });
      
      // Collect events for display
      const newMessages: Message[] = [];
      
      // Create the conversation with custom tools and tool executor
      const conversation = new LocalConversation(agent, workspace, {
        llm,
        systemPrompt: SYSTEM_PROMPT,
        tools: TOOLS,
        toolExecutor,
        maxIterations: 10,
        callback: (event) => {
          // Handle events from the conversation
          // Event properties are spread directly on the event object (not nested under event.data)
          const eventData = event as Record<string, unknown>;
          
          if (eventData.kind === 'assistant_message' && eventData.content) {
            newMessages.push({
              id: `${Date.now()}-assistant`,
              role: 'assistant',
              content: eventData.content as string,
              timestamp: new Date(),
            });
          } else if (eventData.kind === 'tool_call') {
            // Tool calls are handled by toolExecutor
          } else if (eventData.kind === 'tool_result') {
            newMessages.push({
              id: `${Date.now()}-tool`,
              role: 'tool',
              content: eventData.result as string,
              timestamp: new Date(),
              toolName: eventData.tool as string,
            });
          } else if (eventData.kind === 'finish') {
            newMessages.push({
              id: `${Date.now()}-finish`,
              role: 'assistant',
              content: eventData.message as string,
              timestamp: new Date(),
            });
          }
        },
      });
      
      // Start the conversation with the user's message
      await conversation.start({ initialMessage: userMessage.content });
      
      // Run the agent loop
      await conversation.run();
      
      // Update messages with collected events
      setMessages((prev) => [...prev, ...newMessages]);
      
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
  };

  return (
    <div className="chat-container">
      {messages.length === 0 ? (
        <div className="empty-state">
          <div className="icon">🤖</div>
          <h3>Agent Ready</h3>
          <p>
            This agent has access to a <code>console_log</code> tool.
            Try asking it to log something to the console!
          </p>
          <p style={{ marginTop: '1rem', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
            Example: "Log 'Hello, World!' to the console" or "Calculate 2+2 and log the result"
          </p>
        </div>
      ) : (
        <div className="messages">
          {messages.map((message) => (
            <div key={message.id} className={`message ${message.role}`}>
              <div className="message-avatar">
                {message.role === 'user' ? '👤' : message.role === 'tool' ? '🔧' : '🤖'}
              </div>
              <div className="message-content">
                {message.role === 'tool' ? (
                  <div className="tool-result">
                    <div className="tool-header">
                      <span className="tool-icon">🔧</span>
                      <span className="tool-name">{message.toolName}</span>
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
            <div className="message assistant">
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

      <div className="input-area">
        <div className="input-container">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask the agent to do something... (Shift+Enter for new line)"
            disabled={isLoading}
            rows={1}
          />
          <button
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
            <button className="btn btn-secondary" onClick={clearChat} style={{ fontSize: '0.75rem' }}>
              Clear conversation
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
