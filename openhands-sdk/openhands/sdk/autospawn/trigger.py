import os
import shutil
import tempfile
import asyncio
from typing import Dict, Any
import logging

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool

from .models import AgentConfig

from openhands.sdk.workspace.local import LocalWorkspace

logger = logging.getLogger("autospawn")

async def trigger_agent(agent_config: AgentConfig, payload: Dict[str, Any]):
    # Extract repo info from payload
    repo_url = payload.get("repository", {}).get("clone_url")
    if not repo_url:
        logger.warning("No clone_url found in payload")
        return

    # Create temp workspace location
    workspace_dir = tempfile.mkdtemp(prefix="openhands_run_")
    
    # Use LocalWorkspace
    workspace = LocalWorkspace(working_dir=workspace_dir)
    logger.info(f"Created workspace: {workspace_dir}")

    try:
        # Clone repo using subprocess (LocalWorkspace doesn't have clone yet, but we'll use it as context)
        # TODO: Future enhancement: add git clone to LocalWorkspace or use openhands.sdk.git helpers
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", repo_url, ".",
            cwd=workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(f"Failed to clone repo: {stderr.decode()}")
            return

        # Initialize LLM
        # Assuming env vars for API key are set, or provided in config
        llm = LLM(
            model=agent_config.model,
            api_key=os.environ.get("LLM_API_KEY"), # TODO: Make configurable
            base_url=os.environ.get("LLM_BASE_URL"),
        )

        # Initialize Agent
        # TODO: Allow configuring tools
        agent = Agent(
            llm=llm,
            tools=[
                Tool(name=FileEditorTool.name),
                Tool(name=TerminalTool.name),
            ],
        )

        # Initialize Conversation
        conversation = Conversation(agent=agent, workspace=workspace)
        
        # Determine task
        # We can template the task with payload info
        task_prompt = agent_config.task
        
        logger.info(f"Starting conversation with task: {task_prompt}")
        conversation.send_message(task_prompt)
        
        # Run conversation (blocking call in SDK? SDK 0.1 examples uses synchronous .run()?)
        # 01_hello_world.py uses conversation.run() which blocks.
        # We should run this in an executor or thread if valid, or just await if SDK is async.
        # The SDK `Conversation` seems synchronous in the example.
        # We'll run it in a thread to avoid blocking the webhook server.
        await asyncio.to_thread(conversation.run)
        
        logger.info("Conversation finished")

    except Exception as e:
        logger.error(f"Error during agent execution: {e}")
    finally:
        # Cleanup
        # shutil.rmtree(workspace_dir)
        logger.info(f"Workspace kept for debugging: {workspace_dir}")
