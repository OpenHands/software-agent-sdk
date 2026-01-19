# Contributing

Thanks for helping improve the OpenHands Software Agent SDK.

This repo is a foundation. We want the SDK to stay stable and extensible so that many
applications can build on it safely.

Downstream applications we actively keep in mind:
- OpenHands CLI (client)
- OpenHands app-server (client)
- OpenHands SaaS (client)

The SDK itself has a Python interface. Separately, the agent-server exposes the SDK over
REST/WebSockets for remote execution and integrations. Changes should keep both interfaces
stable and consistent.

## A lesson we learned (why we care about architecture)

In earlier iterations, we repeatedly ran into a failure mode: needs from downstream applications
(or assumptions) would leak into core logic.

That kind of coupling can feel convenient in the moment, but it tends to create subtle
breakage elsewhere: different environments, different workspaces, different execution modes,
and different evaluation setups.

Over time we learned the real issue wasn’t individual bugs—it was architecture.

This SDK exists (as a separate, rebuilt foundation) to avoid that failure mode.

## Principles we review PRs against

We try to keep this repo friendly and pragmatic, but we *are* opinionated about several things:

- **SDK-first**: the SDK is the product; downstream apps are clients.
- **No client-specific code paths in core**: avoid logic that only makes sense for one
  downstream app.
- **Prefer interfaces over special cases**: if a client needs something, add or improve a
  clean, reusable interface/extension point instead of adding a shortcut.
- **Extensibility over one-off patches**: design features so multiple clients can adopt them
  without rewriting core logic.
- **Avoid hidden assumptions**: don’t rely on particular env vars, workspace layouts, request
  contexts, or runtime quirks that only exist in one app.
- **Keep the agent loop stable**: treat stability as a feature; be cautious with control-flow
  changes and "small" behavior tweaks.
- **Compatibility is part of the API**: if something could break downstream clients, call it
  out explicitly and consider a migration path.
- **Simple beats clever**: we’d rather merge a straightforward, composable design than a fast
  patch that increases coupling.

If you’re not sure whether a change crosses these lines, ask early—we’re happy to help think
through the shape of a clean interface.

## Questions / discussion

Join us on Slack: https://openhands.dev/joinslack
