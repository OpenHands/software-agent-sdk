/**
 * Confirmation Policy for Conversations
 *
 * Defines policies for when actions require user confirmation before execution.
 * This mirrors the Python SDK's confirmation policy system.
 */

import { ActionEvent } from '../events/types';

/**
 * Risk level for an action
 */
export type RiskLevel = 'low' | 'medium' | 'high' | 'unknown';

/**
 * Convert a risk level to a numeric value for comparison.
 */
export function riskLevelToNumeric(level: RiskLevel): number {
  switch (level) {
    case 'low':
      return 1;
    case 'medium':
      return 2;
    case 'high':
      return 3;
    case 'unknown':
      return 2; // Treat unknown as medium
  }
}

/**
 * Result of a security analysis
 */
export interface SecurityAnalysisResult {
  /** The determined risk level */
  riskLevel: RiskLevel;
  /** Whether confirmation is required */
  requiresConfirmation: boolean;
  /** Human-readable explanation of the risk assessment */
  explanation?: string;
  /** Specific concerns identified */
  concerns?: string[];
}

/**
 * Base interface for confirmation policies.
 * Policies determine when actions require user confirmation.
 */
export interface ConfirmationPolicy {
  /** Policy type identifier */
  readonly type: string;

  /**
   * Determine if an action requires confirmation.
   *
   * @param action - The action to evaluate
   * @param riskLevel - Optional pre-determined risk level
   * @returns True if confirmation is required
   */
  requiresConfirmation(action: ActionEvent, riskLevel?: RiskLevel): boolean;
}

/**
 * Never require confirmation - all actions are auto-approved.
 * This is the default policy for development/testing.
 */
export class NeverConfirm implements ConfirmationPolicy {
  readonly type = 'never';

  requiresConfirmation(_action: ActionEvent, _riskLevel?: RiskLevel): boolean {
    return false;
  }
}

/**
 * Always require confirmation for all actions.
 * Use this for maximum safety/oversight.
 */
export class AlwaysConfirm implements ConfirmationPolicy {
  readonly type = 'always';

  requiresConfirmation(_action: ActionEvent, _riskLevel?: RiskLevel): boolean {
    return true;
  }
}

/**
 * Require confirmation based on risk level.
 * Actions at or above the threshold require confirmation.
 */
export class RiskBasedConfirm implements ConfirmationPolicy {
  readonly type = 'risk_based';
  private threshold: RiskLevel;

  constructor(threshold: RiskLevel = 'medium') {
    this.threshold = threshold;
  }

  requiresConfirmation(_action: ActionEvent, riskLevel?: RiskLevel): boolean {
    const level = riskLevel || 'unknown';
    return riskLevelToNumeric(level) >= riskLevelToNumeric(this.threshold);
  }
}

/**
 * Require confirmation for specific tools.
 */
export class ToolBasedConfirm implements ConfirmationPolicy {
  readonly type = 'tool_based';
  private toolsRequiringConfirmation: Set<string>;

  constructor(tools: string[]) {
    this.toolsRequiringConfirmation = new Set(tools);
  }

  requiresConfirmation(action: ActionEvent, _riskLevel?: RiskLevel): boolean {
    return this.toolsRequiringConfirmation.has(action.tool_name);
  }
}

/**
 * Composite policy - requires confirmation if ANY sub-policy requires it.
 */
export class CompositeConfirm implements ConfirmationPolicy {
  readonly type = 'composite';
  private policies: ConfirmationPolicy[];

  constructor(policies: ConfirmationPolicy[]) {
    this.policies = policies;
  }

  requiresConfirmation(action: ActionEvent, riskLevel?: RiskLevel): boolean {
    return this.policies.some((policy) => policy.requiresConfirmation(action, riskLevel));
  }
}

/**
 * Create a confirmation policy from a type string.
 */
export function createConfirmationPolicy(type: string, options?: unknown): ConfirmationPolicy {
  switch (type) {
    case 'never':
      return new NeverConfirm();
    case 'always':
      return new AlwaysConfirm();
    case 'risk_based':
      return new RiskBasedConfirm((options as { threshold?: RiskLevel })?.threshold);
    case 'tool_based':
      return new ToolBasedConfirm((options as { tools: string[] })?.tools || []);
    default:
      return new NeverConfirm();
  }
}
