from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class AgentConfig(BaseModel):
    task: str
    model: Optional[str] = "gpt-4o"
    image: Optional[str] = "ghcr.io/all-hands-ai/openhands:latest"
    inputs: Optional[Dict[str, Any]] = None

class TriggerConfig(BaseModel):
    event: str  # "pull_request", "issues", "push"
    action: Optional[str] = None # "opened", "labeled", etc.
    repo: str # "owner/repo" or wildcard
    agent: AgentConfig

class AppConfig(BaseModel):
    github_secret: Optional[str] = None
    openhands_api_url: str = "http://localhost:3000"
    triggers: List[TriggerConfig] = []
