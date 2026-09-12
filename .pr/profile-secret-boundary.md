# Profile secret delivery

A profile-only automation launch previously received no saved user secrets, while
Docker materialization did not implement the allow-list introduced in SDK #4931.
The shared selector now filters before any excluded lookup, supplies named saved
secrets, and keeps the profile secret-free at rest. Scoped shell commands use the
selected conversation registry for environment injection and output masking.

Live proof (2026-09-12): started isolated local and Docker Agent Servers from this
branch, saved SELECTED_TOKEN and UNRELATED_TOKEN fixture secrets, and launched the
same profile with secret_refs=[SELECTED_TOKEN] through AsyncAgentServerClient.
The identical runtime command checked that SELECTED_TOKEN was present and
UNRELATED_TOKEN absent, printed the selected value, and exited zero. The returned
output contained <secret-hidden>. Both stored conversations contained only the
selected name and ciphertext decryptable by their respective persistence cipher.
The Docker container was capped at 1500 MB, one CPU, and 192 PIDs. Both disposable
servers were shut down after the probe. No real GitHub credentials were used.

See profile-secret-live.json for conversation IDs and checks. The client helper
comes from SDK #5010; the tested profile, shell, and server behavior is this PR.
Docker probe image: openhands-factory:profile-secrets, built by copying the SDK and
Agent Server packages from this branch onto the existing test runtime image.

Review #4931's profile schema first, then #4966 -> #3403 -> #5008 -> this integration.
The branch merges #4931 to make the two-parent dependency concrete and testable.
After upstream #4931 merges, its unchanged schema/resolver changes disappear from
the integration diff when bases are refreshed.
