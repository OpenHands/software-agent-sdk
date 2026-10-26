import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import {
  Agent,
  ConversationManager,
  RemoteConversation,
  RemoteWorkspace,
} from "../../clients/typescript/dist/index.js";

const options = {
  host: process.env.AGENT_SERVER_URL,
  apiKey: process.env.SESSION_API_KEY,
};
const workingDir = process.argv[2];
const manager = new ConversationManager(options);
const profile = await manager.profiles.getProfile("deepseek-flash", {
  exposeSecrets: "plaintext",
});
const agent = new Agent({
  llm: { ...profile.config, max_output_tokens: 128, num_retries: 0 },
  tools: [],
});
const created = await manager.createConversation(agent, {
  workingDir,
  maxBudgetPerRun: 0.05,
});
assert.equal(
  (await manager.getConversation(created.id)).max_budget_per_run,
  0.05,
);
await created.sendMessage(
  "Call finish immediately with message TYPESCRIPT_BUDGET_OK. Do nothing else.",
);
await created.run();
let info;
for (let attempt = 0; attempt < 120; attempt++) {
  info = await manager.getConversation(created.id);
  if (info.execution_status === "finished" || info.execution_status === "error")
    break;
  await new Promise((resolve) => setTimeout(resolve, 500));
}
assert.equal(info.execution_status, "finished");
const stats = await created.conversationStats();
const cost = Object.values(stats.usage_to_metrics).reduce(
  (sum, value) => sum + value.accumulated_cost,
  0,
);
assert(cost > 0 && cost < 0.05);
assert.deepEqual(stats, info.stats);
const loaded = await manager.loadConversation(created.id, workingDir);
assert.equal(
  (await manager.getConversation(loaded.id)).max_budget_per_run,
  0.05,
);
const fork = await loaded.fork({ reset_metrics: false });
assert.equal((await manager.getConversation(fork.id)).max_budget_per_run, 0.05);
const explicit = new RemoteConversation(
  agent,
  new RemoteWorkspace({ ...options, workingDir }),
  { maxBudgetPerRun: 0.02 },
);
await explicit.start({ maxBudgetPerRun: 0.03 });
assert.equal(
  (await manager.getConversation(explicit.id)).max_budget_per_run,
  0.03,
);
const unlimited = new RemoteConversation(
  agent,
  new RemoteWorkspace({ ...options, workingDir }),
  { maxBudgetPerRun: 0.02 },
);
await unlimited.start({ maxBudgetPerRun: null });
assert.equal(
  (await manager.getConversation(unlimited.id)).max_budget_per_run,
  null,
);
const summary = {
  manager: created.id,
  fork: fork.id,
  explicit: explicit.id,
  unlimited: unlimited.id,
  cap: 0.05,
  cost,
  status: info.execution_status,
  explicit_cap: 0.03,
  null_override: true,
};
await writeFile(
  ".pr/live-review/typescript-summary.json",
  JSON.stringify(summary, null, 2) + "\n",
);
for (const conversation of [unlimited, explicit, fork, loaded, created])
  await conversation.close();
manager.close();
console.log("TYPESCRIPT_BUDGET_OK " + JSON.stringify(summary));
