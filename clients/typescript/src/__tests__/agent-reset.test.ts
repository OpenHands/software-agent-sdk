import { Agent } from '../agent/agent';
import { ConversationClient, type CreateConversationPayload } from '../client/conversation-client';
import { SettingsClient } from '../client/settings-client';
import type { AgentResetCondenser, AgentResetCondenserSettings } from '../index';
import type { AgentServerSettingsPatchRequest } from '../models/agent-server-api';

it('forwards agent reset settings through settings updates and conversation creation', async () => {
  const condenser: AgentResetCondenserSettings = {
    condenser_kind: 'agent_reset',
    enabled: true,
  };
  const fetchMock = vi.fn().mockImplementation(async () =>
    Response.json({
      agent_settings: { condenser },
      conversation_settings: {},
      llm_api_key_is_set: false,
    })
  );
  vi.stubGlobal('fetch', fetchMock);
  try {
    const settings = new SettingsClient({ host: 'https://agent.test' });
    const patch: AgentServerSettingsPatchRequest = {
      agent_settings_diff: { condenser },
    };
    const updated = await settings.updateSettings(patch);
    expect(updated.agent_settings.condenser).toEqual(condenser);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'https://agent.test/api/settings',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify(patch) })
    );

    const client = new ConversationClient({ host: 'https://agent.test' });
    const payload: CreateConversationPayload = {
      agent_settings: { agent_kind: 'openhands', condenser, tools: [] },
    };
    await client.createConversation(payload);
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'https://agent.test/api/conversations',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(payload) })
    );

    const runtimeCondenser: AgentResetCondenser = { kind: 'AgentResetCondenser' };
    const direct: CreateConversationPayload = {
      agent: new Agent({
        llm: { model: 'test-model' },
        condenser: runtimeCondenser,
        include_default_tools: ['NewContextTool', 'ConversationHistoryTool'],
      }),
    };
    await client.createConversation(direct);
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'https://agent.test/api/conversations',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(direct) })
    );
  } finally {
    vi.unstubAllGlobals();
  }
});
