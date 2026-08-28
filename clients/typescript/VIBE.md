# Vibecoding with the OpenHands typescript-client

This client is great for vibecoding small custom frontends that drive agents. Below are some instructions
you can pass to an agent to bootstrap a new repo.

> You are working with @openhands/typescript-client to build a custom frontend for working with
> agents that can write and run code. Clone github.com/openhands/typescript-client and investigate the API.
>
> To start this frontend, create a basic React application. On the homepage should be a "settings" button, which
> opens a modal. These four fields should be settable:
> * AgentServer URL
> * AgentServer API Key
> * LLM model
> * LLM key
> In the modal, show whether the AgentServer is connected by trying to access http://174.138.76.92:8000/api/conversations/count, and make sure it responds 2xx
>
> Store all this in localStorage. If no settings are found on page load, open the modal by default
>
> If the settings are found and working, start a new conversation using the typescript-client. Show a message box on the homepage which allows sending messages
> to the agent, and show the most recent message back from the agent below that
>
> Set up GitHub actions to build, test, and lint all this

