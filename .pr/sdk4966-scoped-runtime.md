# Conversation-scoped runtime access: live Canvas evidence

![Real Canvas scenario](sdk4966-scoped-runtime.gif)

Canvas opened the actual fixture.json written by the real agent, first in a local workspace and then in Docker. Observed file reads use /api/conversations/<id>/bash/execute_bash_command and return200 through the SDK/TypeScript transport. The agent and file pane show the matching selected/empty results.

[Exact revisions and allowlisted observations](sdk4966-scoped-runtime.json). This was a disposable Canvas configured through UI operations, using a real DeepSeek v4 Flash agent, two harmless synthetic saved secrets, and an uploaded test fixture. No repository or GitHub credential was used. The bundle attached through the public SDK. Its SHA256 was `ffaa73c82742204e7e84e29e92add8b911ec074e9359a0881ace29f80587e234`.

Integrated after demonstration: SDK `ac6d12b0b9d76f1cc38a6eb1ea51cd92e34a0bf2`, Automation `8abaa0b5fdea18132c68ef71dfd645c5aafcc13a`, Canvas `4cd1513bbe74abd2c1f9e58a77cf64375aafe001`. Early local queue/profile frames used Canvas `a3c7915`; final conversation/file frames include the later combined Logs/onboarding consumer refresh. This is direct enhancement evidence with companion changes identified, not a before/after claim.

Limits: This demonstrates scoped file/terminal transport, not every Git/editor capability. Docker lifecycle belongs to3403; public attachment orchestration uses5010.

A private3GiB memory guard interrupted the comparison twice: one local run after its agent finished (explicitly cancelled and retried), and lifecycle inspection after both Docker jobs completed. Successful retry and lifecycle captures were recorded after resources recovered. Disconnection/capture-selector retry frames are excluded. All private services and containers were stopped afterward; the running software factory was untouched.
