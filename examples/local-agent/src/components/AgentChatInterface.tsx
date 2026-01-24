import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import type { OpenRouterLLM, Tool, ToolCall, ChatMessage } from '@openhands/typescript-client';

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
  llm: OpenRouterLLM;
  model: string;
}

// Define the console_log tool
const TOOLS: Tool[] = [
  {
    type: 'function',
    function: {
      name: 'console_log',
      description: 'Logs a message to the console. Use this to output information, debug values, or display results to the user.',
      parameters: {
        type: 'object',
        properties: {
          message: {
            type: 'string',
            description: 'The message to log to the console',
          },
        },
        required: ['message'],
      },
    },
  },
];

// Execute a tool call
function executeToolCall(toolCall: ToolCall): string {
  const { name, arguments: argsString } = toolCall.function;
  
  try {
    const args = JSON.parse(argsString);
    
    if (name === 'console_log') {
      const message = args.message || '';
      console.log('[Agent Tool]', message);
      return `Logged to console: "${message}"`;
    }
    
    return `Unknown tool: ${name}`;
  } catch (error) {
    return `Error executing tool: ${error instanceof Error ? error.message : 'Unknown error'}`;
  }
}

const SYSTEM_PROMPT = `You are a helpful AI assistant with access to tools. When the user asks you to do something that requires outputting information, use the console_log tool to display it.

Available tools:
- console_log: Logs a message to the browser console. Use this to output results, display information, or show computed values.

When you need to show something to the user, use the console_log tool. After using tools, summarize what you did for the user.`;

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

  const runAgentLoop = async (conversationMessages: ChatMessage[]): Promise<Message[]> => {
    const newMessages: Message[] = [];
    let currentMessages = [...conversationMessages];
    const maxIterations = 10; // Prevent infinite loops
    
    for (let i = 0; i < maxIterations; i++) {
      const response = await llm.chatCompletion({
        messages: currentMessages,
        model,
        tools: TOOLS,
        toolChoice: 'auto',
      });

      const choice = response.choices[0];
      if (!choice) break;

      const assistantMessage = choice.message;
      
      // Add assistant message to our display
      if (assistantMessage.content || assistantMessage.tool_calls) {
        const displayMessage: Message = {
          id: `${Date.now()}-${i}-assistant`,
          role: 'assistant',
          content: assistantMessage.content || '',
          timestamp: new Date(),
          toolCalls: assistantMessage.tool_calls,
        };
        newMessages.push(displayMessage);
        
        // Add to conversation for next iteration
        currentMessages.push({
          role: 'assistant',
          content: assistantMessage.content || '',
          tool_calls: assistantMessage.tool_calls,
        });
      }

      // If no tool calls, we're done
      if (!assistantMessage.tool_calls || assistantMessage.tool_calls.length === 0) {
        break;
      }

      // Execute tool calls and add results
      for (const toolCall of assistantMessage.tool_calls) {
        const result = executeToolCall(toolCall);
        
        // Add tool result to display
        const toolMessage: Message = {
          id: `${Date.now()}-${i}-tool-${toolCall.id}`,
          role: 'tool',
          content: result,
          timestamp: new Date(),
          toolCallId: toolCall.id,
          toolName: toolCall.function.name,
        };
        newMessages.push(toolMessage);
        
        // Add to conversation for next iteration
        currentMessages.push({
          role: 'tool',
          content: result,
          tool_call_id: toolCall.id,
        });
      }
    }
    
    return newMessages;
  };

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
      // Build conversation history
      const conversationMessages: ChatMessage[] = [
        { role: 'system', content: SYSTEM_PROMPT },
        ...messages.map((msg): ChatMessage => {
          if (msg.role === 'tool') {
            return {
              role: 'tool',
              content: msg.content,
              tool_call_id: msg.toolCallId || '',
            };
          }
          return {
            role: msg.role as 'user' | 'assistant',
            content: msg.content,
            tool_calls: msg.toolCalls,
          };
        }),
        { role: 'user', content: userMessage.content },
      ];

      // Run the agent loop
      const newMessages = await runAgentLoop(conversationMessages);
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
