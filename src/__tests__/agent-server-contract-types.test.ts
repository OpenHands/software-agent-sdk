import type { SettingsClient } from '../client/settings-client';
import type {
  AgentServerSettingsPatchRequest,
  AgentServerSettingsPatchResponse,
  AgentServerSettingsResponse,
  AgentServerSettingsSchema,
} from '../models/agent-server-api';

type Equal<Left, Right> =
  (<Value>() => Value extends Left ? 1 : 2) extends <Value>() => Value extends Right ? 1 : 2
    ? true
    : false;

function assertType<Type extends true>(_value: Type): void {
  // Compiling this call is the assertion.
}

describe('Agent Server generated contract aliases', () => {
  it('statically checks settings client request and response types', () => {
    assertType<
      Equal<Awaited<ReturnType<SettingsClient['getAgentSchema']>>, AgentServerSettingsSchema>
    >(true);
    assertType<
      Equal<Awaited<ReturnType<SettingsClient['getSettings']>>, AgentServerSettingsResponse>
    >(true);
    assertType<
      Equal<Parameters<SettingsClient['updateSettings']>[0], AgentServerSettingsPatchRequest>
    >(true);
    assertType<
      Equal<Awaited<ReturnType<SettingsClient['updateSettings']>>, AgentServerSettingsPatchResponse>
    >(true);
  });
});
