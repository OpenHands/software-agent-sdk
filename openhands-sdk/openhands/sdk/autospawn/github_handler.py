import logging
from .config import current_config
from .trigger import trigger_agent
from typing import Dict, Any

logger = logging.getLogger("autospawn")

async def handle_github_event(event_type: str, payload: Dict[str, Any]):
    repo_full_name = payload.get("repository", {}).get("full_name")
    action = payload.get("action")
    
    logger.info(f"Received event: {event_type} action: {action} repo: {repo_full_name}")
    
    for trigger in current_config.triggers:
        # Check event type match
        if trigger.event != event_type:
            continue
            
        # Check action match (if specified)
        if trigger.action and trigger.action != action:
            continue
            
        # Check repo match (simple string match for now)
        if trigger.repo != repo_full_name:
            continue
            
        logger.info(f"Trigger matched! Spawning agent for task: {trigger.agent.task}")
        await trigger_agent(trigger.agent, payload)
