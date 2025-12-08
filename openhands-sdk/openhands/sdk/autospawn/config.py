import yaml
import os
from .models import AppConfig

def load_config(path: str = "config.yaml") -> AppConfig:
    if not os.path.exists(path):
        return AppConfig()
    
    with open(path, "r") as f:
        raw_config = yaml.safe_load(f)
    
    # Resolve env vars in config (simple implementation)
    # in a real app, we might want more robust env substitution
    if raw_config.get("github_secret", "").startswith("env:"):
        env_var = raw_config["github_secret"].split(":", 1)[1]
        raw_config["github_secret"] = os.environ.get(env_var)
        
    return AppConfig(**raw_config)

current_config = load_config()
