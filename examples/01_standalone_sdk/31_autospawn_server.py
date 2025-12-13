import uvicorn
import os
import sys

# Ensure we can import from local sdk
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "openhands-sdk"))

from openhands.sdk.autospawn import app

# This example assumes a config.yaml exists in current dir
if __name__ == "__main__":
    print("Starting OpenHands Autospawn Server...")
    print("Ensure you have a 'config.yaml' in the current directory.")
    print("Send webhooks to http://localhost:8000/webhooks/github")
    uvicorn.run(app, host="127.0.0.1", port=8000)
