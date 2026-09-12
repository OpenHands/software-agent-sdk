# Agent Server orchestration clients

The factory exposed a gap between the high-level Conversation/Workspace interfaces and trusted control-plane operations: saved-profile creation, selected-runtime credentials, release preserving history, and nonblocking command dispatch. Consumers currently encode these HTTP contracts themselves.

Add public sync/async AgentServerClient and scoped RuntimeClient interfaces in the SDK. Share operation construction in one internal module, so synchronous and asynchronous callers use identical routes, parameters, and auth. Keep scheduling, admission, retry policy, workflow prompts, and acceptance decisions in their owning consumers. Retain existing Conversation/Workspace behavior without migration.

Server-level metadata and conversation creation remain global. A runtime binds a validated conversation UUID and exposes named methods rather than arbitrary URL access. Explicit legacy host-runtime access supports existing dispatch consumers; lifecycle methods reject that unscoped form. A narrow API-prefix migration helper validates old caller state rather than trusting arbitrary paths.

Response objects retain additive server fields. The credential accessor validates a nonempty key. Release treats 404 as already released but propagates stop failures. Injected HTTP clients stay caller-owned; otherwise close/aclose releases the client pool.

Server contract review precedes this consumer: #4966/#3403, #4998/#5005, #5008. The client is an additive main-based PR; it changes none of those server implementations. Automation and extension consumers will use these methods and remove raw runtime HTTP code.
