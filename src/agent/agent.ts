/**
 * Agent class that provides a constructor-based API for creating agents.
 * Provides a cleaner API that matches the Python SDK naming.
 */

import { AgentBase, LLM } from '../types/base';

export interface AgentOptions {
  llm: LLM;
  kind?: string;
  name?: string;
  [key: string]: any;
}

/**
 * Agent class that implements AgentBase interface.
 * Provides a constructor-based API for creating agents.
 *
 * Usage:
 *   const agent = new Agent({
 *     llm: {
 *       model: 'gpt-4',
 *       api_key: 'your-key'
 *     }
 *   });
 */
export class Agent implements AgentBase {
  kind: string;
  llm: LLM;
  name?: string;
  [key: string]: any;

  constructor(options: AgentOptions) {
    this.kind = options.kind || 'Agent';
    this.llm = options.llm;
    this.name = options.name;

    // Copy any additional properties
    Object.keys(options).forEach((key) => {
      if (key !== 'kind' && key !== 'llm' && key !== 'name') {
        this[key] = options[key];
      }
    });
  }
}
