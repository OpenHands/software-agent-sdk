import { pathToFileURL } from 'node:url';

const clientsModule = process.env.CLIENTS_MODULE;
const agentServerHost = process.env.AGENT_SERVER_URL ?? 'http://127.0.0.1:8010';
const cloudHost = process.env.CLOUD_HOST ?? 'https://app.all-hands.dev';
const cloudApiKey = process.env.CLOUD_API_KEY;

if (!clientsModule) {
  throw new Error('CLIENTS_MODULE must point to the built clients.js under test');
}

const traces = [];
const realFetch = globalThis.fetch;
globalThis.fetch = async (input, init = {}) => {
  const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
  const method = init.method ?? (input instanceof Request ? input.method : 'GET');
  const sanitizedPath = url.pathname.replace(/^\/api\/keys\/\d+$/, '/api/keys/{id}');
  const started = performance.now();
  try {
    const response = await realFetch(input, init);
    traces.push({
      method,
      host: url.host,
      path: sanitizedPath,
      status: response.status,
      elapsed_ms: Number((performance.now() - started).toFixed(1)),
    });
    return response;
  } catch (error) {
    traces.push({
      method,
      host: url.host,
      path: sanitizedPath,
      error: error instanceof Error ? error.name : typeof error,
      elapsed_ms: Number((performance.now() - started).toFixed(1)),
    });
    throw error;
  }
};

const clients = await import(pathToFileURL(clientsModule).href);
const exportAvailability = Object.fromEntries(
  ['OpenHandsClient', 'AgentServerClient', 'CloudClient', 'startDeviceFlow', 'pollForToken'].map(
    (name) => [name, typeof clients[name] !== 'undefined']
  )
);

const legacyServer = new clients.ServerClient({ host: agentServerHost, timeout: 5000 });
const legacyBash = new clients.BashClient({ host: agentServerHost, timeout: 5000 });
const legacyHealth = await legacyServer.getHealth();
const legacyInfo = await legacyServer.getServerInfo();
const legacyBashOutput = await legacyBash.executeCommand('printf legacy-endpoint-ok', '/workspace', 5);

const result = {
  commit: process.env.TESTED_COMMIT ?? null,
  module: clientsModule,
  exports: exportAvailability,
  legacy: {
    health: legacyHealth.status,
    serverVersion: legacyInfo.version,
    bashExitCode: legacyBashOutput.exit_code,
    bashStdout: legacyBashOutput.stdout,
  },
};

if (clients.AgentServerClient) {
  const aggregate = new clients.AgentServerClient({ host: agentServerHost, timeout: 5000 });
  const namespacedHealth = await aggregate.server.getHealth();
  const directHealth = await aggregate.get('/health');
  const aggregateBashOutput = await aggregate.bash.executeCommand(
    'printf aggregate-endpoint-ok',
    '/workspace',
    5
  );
  let transportError;
  try {
    const unavailable = new clients.AgentServerClient({
      host: 'http://127.0.0.1:9',
      timeout: 250,
    });
    await unavailable.get('/health');
  } catch (error) {
    transportError = {
      name: error instanceof Error ? error.name : typeof error,
      messageHasRequestFailure:
        error instanceof Error && /request failed|fetch failed|timeout/i.test(error.message),
    };
  }
  result.aggregateAgentServer = {
    kind: aggregate.kind,
    namespacedHealth: namespacedHealth.status,
    directHealth: directHealth.status,
    bashExitCode: aggregateBashOutput.exit_code,
    bashStdout: aggregateBashOutput.stdout,
    transportError,
  };
  aggregate.close();
}

if (clients.CloudClient && cloudApiKey) {
  const cloud = new clients.CloudClient({ host: cloudHost, apiKey: cloudApiKey, timeout: 10000 });
  const currentKey = await cloud.getCurrentApiKey();
  const organizations = await cloud.getOrganizations();
  const settings = await cloud.getSettingsWithDerivedFields();
  let httpError;
  try {
    await cloud.post('/api/keys/current');
  } catch (error) {
    httpError = {
      name: error instanceof Error ? error.name : typeof error,
      status: typeof error?.status === 'number' ? error.status : null,
    };
  }
  result.aggregateCloud = {
    kind: cloud.kind,
    authTypePresent: typeof currentKey.auth_type === 'string' && currentKey.auth_type.length > 0,
    orgIdPresent: typeof currentKey.org_id === 'string' && currentKey.org_id.length > 0,
    organizationCount: organizations.items.length,
    currentOrgPresent: typeof organizations.currentOrgId === 'string',
    agentSettingsDerived: typeof settings.agent_settings === 'object',
    conversationSettingsDerived: typeof settings.conversation_settings === 'object',
    httpError,
  };

  const device = await cloud.startDeviceFlow();
  const verification = await fetch(`${cloudHost}/oauth/device/verify-authenticated`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${cloudApiKey}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams({ user_code: device.user_code }),
  });
  if (!verification.ok) {
    throw new Error(`Device verification failed with HTTP ${verification.status}`);
  }
  const linkedToken = await cloud.pollForToken(device.device_code, {
    interval: device.interval,
    timeout: 15000,
  });
  const linkedCloud = new clients.CloudClient({
    host: cloudHost,
    apiKey: linkedToken.access_token,
    timeout: 10000,
  });
  const linkedKey = await linkedCloud.getCurrentApiKey();
  await linkedCloud.delete(`/api/keys/${linkedKey.id}`);
  let deletedTokenStatus;
  try {
    await linkedCloud.getCurrentApiKey();
  } catch (error) {
    deletedTokenStatus = typeof error?.status === 'number' ? error.status : null;
  }
  result.deviceFlow = {
    startSucceeded: true,
    verificationHost: new URL(device.verification_uri).host,
    completeUriPresent: typeof device.verification_uri_complete === 'string',
    expiresIn: device.expires_in,
    interval: device.interval,
    authenticatedVerificationStatus: verification.status,
    pollReturnedBearer: linkedToken.token_type === 'Bearer',
    returnedKeyRecognized:
      typeof linkedKey.id === 'number' &&
      typeof linkedKey.user_id === 'string' &&
      linkedKey.user_id.length > 0,
    returnedKeyCleanedUp: deletedTokenStatus === 401,
  };
  linkedCloud.close();
  cloud.close();
}

legacyServer.close();
legacyBash.close();
result.traces = traces;
console.log(JSON.stringify(result, null, 2));
