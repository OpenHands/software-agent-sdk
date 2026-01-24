/**
 * Code Review Agent Example
 *
 * An example agent that reviews code in a directory and provides feedback.
 * Demonstrates how to customize the system prompt and run a specific task.
 *
 * Usage:
 *   export OPENROUTER_API_KEY="your-api-key"
 *   npx ts-node examples/local/code-review-agent.ts [directory-to-review]
 */

// When running from source: import from '../../src'
// When using the package: import from '@openhands/typescript-client'
import {
  LocalWorkspace,
  LocalConversation,
  OpenRouterLLM,
  AgentBase,
  Event,
} from '../../dist';

// Custom system prompt for code review
const CODE_REVIEW_PROMPT = `You are an expert code reviewer. Your task is to review code and provide constructive feedback.

<ROLE>
* Review code for bugs, security issues, performance problems, and code quality
* Suggest improvements and best practices
* Be constructive and educational in your feedback
* Prioritize issues by severity (critical, major, minor, suggestion)
</ROLE>

<REVIEW_CHECKLIST>
1. **Bugs & Logic Errors**: Look for potential bugs, off-by-one errors, null checks, etc.
2. **Security**: Check for security vulnerabilities (injection, XSS, hardcoded secrets, etc.)
3. **Performance**: Identify performance bottlenecks or inefficient code
4. **Code Quality**: Check naming conventions, code organization, DRY principle
5. **Error Handling**: Verify proper error handling and edge cases
6. **Documentation**: Check if code is adequately commented/documented
7. **Testing**: Note if tests are missing for critical functionality
</REVIEW_CHECKLIST>

<OUTPUT_FORMAT>
Structure your review as:
1. **Summary**: Brief overview of what the code does
2. **Critical Issues**: Must-fix problems (if any)
3. **Major Issues**: Should-fix problems
4. **Minor Issues**: Nice-to-fix suggestions
5. **Positive Notes**: What was done well
6. **Recommendations**: General suggestions for improvement
</OUTPUT_FORMAT>

<TOOLS>
* Use execute_command to explore the codebase (ls, find, grep, etc.)
* Use read_file to examine specific files
* Use think to organize your thoughts before providing feedback
* Use finish when you've completed the review with your findings
</TOOLS>`;

async function main() {
  // Get API key
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) {
    console.error('Error: OPENROUTER_API_KEY environment variable is not set');
    console.error('Get your API key at: https://openrouter.ai');
    process.exit(1);
  }

  // Get directory to review
  const targetDir = process.argv[2] || process.cwd();

  console.log('═'.repeat(60));
  console.log('            Code Review Agent');
  console.log('═'.repeat(60));
  console.log();
  console.log(`📁 Target Directory: ${targetDir}`);
  console.log(`🤖 Model: anthropic/claude-3.5-sonnet`);
  console.log();

  // Create components
  const llm = new OpenRouterLLM({
    apiKey,
    defaultModel: 'anthropic/claude-3.5-sonnet',
    defaultTemperature: 0.3, // Lower temperature for more focused analysis
    defaultMaxTokens: 8192,   // Higher token limit for detailed reviews
  });

  const workspace = new LocalWorkspace({ workingDir: targetDir });

  const agent: AgentBase = {
    kind: 'code-review-agent',
    llm: { model: 'anthropic/claude-3.5-sonnet' },
  };

  // Track output for final summary
  let finalReview = '';

  const conversation = new LocalConversation(agent, workspace, {
    llm,
    maxIterations: 30,
    systemPrompt: CODE_REVIEW_PROMPT,
    callback: (event: Event) => {
      switch (event.kind) {
        case 'tool_call':
          const tool = (event as any).tool;
          if (tool === 'execute_command') {
            try {
              const args = JSON.parse((event as any).arguments);
              console.log(`\n📎 Running: ${args.command}`);
            } catch {
              console.log(`\n🔧 ${tool}`);
            }
          } else if (tool === 'read_file') {
            try {
              const args = JSON.parse((event as any).arguments);
              console.log(`\n📄 Reading: ${args.path}`);
            } catch {
              console.log(`\n📄 Reading file...`);
            }
          } else if (tool === 'think') {
            console.log(`\n🤔 Analyzing...`);
          }
          break;
        case 'tool_result':
          const resultTool = (event as any).tool;
          if (resultTool === 'execute_command') {
            const result = (event as any).result;
            // Show truncated command output
            if (result.length > 200) {
              console.log(`   ${result.slice(0, 200)}... (truncated)`);
            } else {
              console.log(`   ${result}`);
            }
          }
          break;
        case 'think':
          console.log(`\n🤔 ${(event as any).thought.slice(0, 100)}...`);
          break;
        case 'finish':
          finalReview = (event as any).message;
          break;
        case 'agent_error':
          console.error(`\n❌ Error: ${(event as any).error}`);
          break;
      }
    },
  });

  console.log('🔍 Starting code review...\n');
  console.log('─'.repeat(60));

  // Start and run the review
  await conversation.start({
    initialMessage: `Please review the code in this directory. Start by exploring the file structure, then examine the main source files, and provide a comprehensive code review following the checklist and output format in your instructions.`,
  });

  try {
    await conversation.run();
  } catch (error) {
    console.error('Review error:', error);
  }

  // Print final review
  console.log('\n' + '═'.repeat(60));
  console.log('                    CODE REVIEW REPORT');
  console.log('═'.repeat(60));
  console.log();
  console.log(finalReview);
  console.log();
  console.log('═'.repeat(60));

  // Print stats
  const stats = await conversation.conversationStats();
  console.log(`\n📊 Review completed: ${stats.total_events} events, ${stats.action_events} actions`);

  await conversation.close();
}

main().catch(console.error);
