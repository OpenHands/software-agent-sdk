import { deriveSwitchPlan } from '../profiles/derive-switch-plan';
import { ACP_PROVIDERS } from '../models/acp';
import type { ACPAgentProfile, OpenHandsAgentProfile } from '../models/agent-profile';
import type { ACPProviderInfo } from '../models/acp';

const baseVerification = {
  critic_enabled: false,
  critic_mode: 'finish_and_message',
  enable_iterative_refinement: false,
  critic_threshold: 0.6,
  max_refinement_iterations: 3,
  critic_server_url: null,
  critic_model_name: null,
};

const ohBase: OpenHandsAgentProfile = {
  id: 'profile-1',
  name: 'default',
  revision: 0,
  agent_kind: 'openhands',
  llm_profile_ref: 'gpt-4o',
  mcp_server_refs: null,
  agent: 'CodeActAgent',
  skills: [],
  system_message_suffix: null,
  condenser: {},
  verification: baseVerification,
  enable_sub_agents: false,
  tool_concurrency_limit: 1,
};

const acpBase: ACPAgentProfile = {
  id: 'profile-2',
  name: 'claude-default',
  revision: 0,
  agent_kind: 'acp',
  acp_server: 'claude-code',
  acp_model: 'claude-sonnet-4-6',
  acp_session_mode: null,
  acp_prompt_timeout: 1800,
  acp_command: null,
  acp_args: null,
  mcp_server_refs: null,
};

const claudeProvider: ACPProviderInfo = ACP_PROVIDERS['claude-code'];
const codexProvider: ACPProviderInfo = ACP_PROVIDERS['codex'];

describe('deriveSwitchPlan', () => {
  describe('null snapshot', () => {
    it('returns start-new when no snapshot', () => {
      const plan = deriveSwitchPlan(null, ohBase, null);
      expect(plan.action).toBe('start-new');
    });

    it('returns start-new when snapshot is undefined', () => {
      const plan = deriveSwitchPlan(undefined, ohBase, null);
      expect(plan.action).toBe('start-new');
    });
  });

  describe('current', () => {
    it('returns current when same id and revision', () => {
      const plan = deriveSwitchPlan(ohBase, ohBase, null);
      expect(plan.action).toBe('current');
    });

    it('returns current when ACP same id and revision', () => {
      const plan = deriveSwitchPlan(acpBase, acpBase, claudeProvider);
      expect(plan.action).toBe('current');
    });

    it('returns current when OpenHands content identical but different object', () => {
      const target = { ...ohBase };
      const plan = deriveSwitchPlan(ohBase, target, null);
      expect(plan.action).toBe('current');
    });
  });

  describe('kind change', () => {
    it('returns start-new when switching from OpenHands to ACP', () => {
      const plan = deriveSwitchPlan(ohBase, acpBase, claudeProvider);
      expect(plan.action).toBe('start-new');
      if (plan.action === 'start-new') {
        expect(plan.reason).toMatch(/agent kind/i);
      }
    });

    it('returns start-new when switching from ACP to OpenHands', () => {
      const plan = deriveSwitchPlan(acpBase, ohBase, claudeProvider);
      expect(plan.action).toBe('start-new');
      if (plan.action === 'start-new') {
        expect(plan.reason).toMatch(/agent kind/i);
      }
    });
  });

  describe('OpenHands → OpenHands', () => {
    it('switch-live when only llm_profile_ref differs', () => {
      const target: OpenHandsAgentProfile = {
        ...ohBase,
        revision: 1,
        llm_profile_ref: 'claude-sonnet-4-6',
      };
      const plan = deriveSwitchPlan(ohBase, target, null);
      expect(plan.action).toBe('switch-live');
      if (plan.action === 'switch-live') {
        expect(plan.mutableFields).toEqual(['llm_profile_ref']);
      }
    });

    it('start-new when agent class also changes', () => {
      const target: OpenHandsAgentProfile = {
        ...ohBase,
        revision: 1,
        llm_profile_ref: 'claude-sonnet-4-6',
        agent: 'BrowsingAgent',
      };
      const plan = deriveSwitchPlan(ohBase, target, null);
      expect(plan.action).toBe('start-new');
      if (plan.action === 'start-new') {
        expect(plan.reason).toContain('agent');
      }
    });

    it('start-new when tool_concurrency_limit changes', () => {
      const target: OpenHandsAgentProfile = { ...ohBase, revision: 1, tool_concurrency_limit: 4 };
      const plan = deriveSwitchPlan(ohBase, target, null);
      expect(plan.action).toBe('start-new');
    });

    it('start-new when enable_sub_agents changes', () => {
      const target: OpenHandsAgentProfile = { ...ohBase, revision: 1, enable_sub_agents: true };
      const plan = deriveSwitchPlan(ohBase, target, null);
      expect(plan.action).toBe('start-new');
    });
  });

  describe('ACP → ACP (same provider)', () => {
    it('switch-live when only acp_model differs and provider supports it', () => {
      const target: ACPAgentProfile = { ...acpBase, revision: 1, acp_model: 'claude-opus-4-8' };
      const plan = deriveSwitchPlan(acpBase, target, claudeProvider);
      expect(plan.action).toBe('switch-live');
      if (plan.action === 'switch-live') {
        expect(plan.mutableFields).toEqual(['acp_model']);
      }
    });

    it('start-new when only acp_model differs but provider does NOT support runtime switch', () => {
      const codexSnapshot: ACPAgentProfile = {
        ...acpBase,
        acp_server: 'codex',
        acp_model: 'gpt-5',
      };
      const target: ACPAgentProfile = { ...codexSnapshot, revision: 1, acp_model: 'o4' };
      const plan = deriveSwitchPlan(codexSnapshot, target, codexProvider);
      // codex supports_runtime_model_switch check
      const expected = codexProvider.supports_runtime_model_switch ? 'switch-live' : 'start-new';
      expect(plan.action).toBe(expected);
    });

    it('start-new when providerInfo is null and acp_model differs', () => {
      const target: ACPAgentProfile = { ...acpBase, revision: 1, acp_model: 'claude-opus-4-8' };
      const plan = deriveSwitchPlan(acpBase, target, null);
      expect(plan.action).toBe('start-new');
      if (plan.action === 'start-new') {
        expect(plan.reason).toMatch(/runtime model switch/i);
      }
    });

    it('start-new when acp_session_mode also changes', () => {
      const target: ACPAgentProfile = {
        ...acpBase,
        revision: 1,
        acp_model: 'claude-opus-4-8',
        acp_session_mode: 'bypassPermissions',
      };
      const plan = deriveSwitchPlan(acpBase, target, claudeProvider);
      expect(plan.action).toBe('start-new');
      if (plan.action === 'start-new') {
        expect(plan.reason).toContain('acp_session_mode');
      }
    });

    it('current when ACP content identical but id+revision differ (fast-path skipped)', () => {
      const target: ACPAgentProfile = { ...acpBase, id: 'profile-9', revision: 5 };
      const plan = deriveSwitchPlan(acpBase, target, claudeProvider);
      expect(plan.action).toBe('current');
    });

    it('current when ACP content identical even if provider lacks runtime switch', () => {
      // Regression: an unchanged profile must be "current" regardless of the
      // provider's runtime-switch capability. providerInfo=null (custom server)
      // previously returned a spurious "start-new".
      const target: ACPAgentProfile = { ...acpBase, id: 'profile-9', revision: 5 };
      const plan = deriveSwitchPlan(acpBase, target, null);
      expect(plan.action).toBe('current');
    });
  });

  describe('ACP → ACP (provider change)', () => {
    it('start-new when acp_server changes', () => {
      const target: ACPAgentProfile = { ...acpBase, revision: 1, acp_server: 'codex' };
      const plan = deriveSwitchPlan(acpBase, target, claudeProvider);
      expect(plan.action).toBe('start-new');
      if (plan.action === 'start-new') {
        expect(plan.reason).toMatch(/provider/i);
      }
    });
  });
});
