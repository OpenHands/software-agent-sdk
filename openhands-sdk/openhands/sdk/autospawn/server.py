try:
    from fastapi import FastAPI, Request, HTTPException
except ImportError:
    raise ImportError("FastAPI is required for autospawn. Install with pip install 'openhands-sdk[server]'")

from .config import current_config
from .github_handler import handle_github_event
import logging

app = FastAPI()
logger = logging.getLogger("autospawn")
# logging.basicConfig(level=logging.INFO) # Library shouldn't control basicConfig

@app.post("/webhooks/github")
async def github_webhook(request: Request):
    # Verify signature
    signature = request.headers.get("X-Hub-Signature-256")
    if current_config.github_secret:
        # TODO: Implement HMAC verification
        pass
    
    event_type = request.headers.get("X-GitHub-Event")
    if not event_type:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")
    
    payload = await request.json()
    
    try:
        await handle_github_event(event_type, payload)
    except Exception as e:
        logger.error(f"Error handling event: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
        
    return {"status": "ok"}
