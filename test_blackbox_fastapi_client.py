#!/usr/bin/env python3
"""
Black box integration test for issue #5205 using FastAPI TestClient.

Tests that deleting an active ACP profile properly resets agent_settings
to prevent stale ACP configuration from persisting.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# Add the SDK to the path
sys.path.insert(0, str(Path(__file__).parent / "openhands-sdk"))
sys.path.insert(0, str(Path(__file__).parent / "openhands-tools"))
sys.path.insert(0, str(Path(__file__).parent / "openhands-workspace"))
sys.path.insert(0, str(Path(__file__).parent / "openhands-agent-server"))

from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import reset_stores


def test_bug_5205():
    """
    Reproduce and verify fix for bug #5205.
    
    Steps:
    1. Create and activate an ACP profile (e.g., Codex)
    2. Set agent_settings to have ACP configuration
    3. Delete the ACP profile
    4. Verify agent_settings is reset to default OpenHands (not stale ACP)
    """
    print("\n" + "="*70)
    print("BLACK BOX TEST: Issue #5205 - ACP Profile Deletion")
    print("="*70)
    
    # Create temporary directory for persistence
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # Set up environment
        os.environ["OH_PERSISTENCE_DIR"] = str(tmpdir_path)
        os.environ["OPENHANDS_SUPPRESS_BANNER"] = "1"
        
        # Reset stores to ensure clean state
        reset_stores()
        
        # Create test client
        config = Config(
            static_files_path=None,
            session_api_keys=[],
            secret_key=None
        )
        app = create_app(config)
        client = TestClient(app)
        
        # Step 1: Create an ACP profile
        print("\n[1] Creating ACP profile 'codex-blackbox-test'...")
        profile_data = {
            "agent_kind": "acp",
            "acp_server": "codex",
            "acp_model": "gpt-5.5",
        }
        response = client.post("/api/agent-profiles/codex-blackbox-test", json=profile_data)
        assert response.status_code == 201, f"Failed to create profile: {response.status_code} {response.text}"
        print(f"✓ Created ACP profile: {response.json()['name']}")
        
        # Get the profile ID
        response = client.get("/api/agent-profiles/codex-blackbox-test")
        assert response.status_code == 200
        profile_id = response.json()["profile"]["id"]
        print(f"  Profile ID: {profile_id}")
        
        # Step 2: Activate the ACP profile
        print("\n[2] Activating ACP profile...")
        response = client.post(f"/api/agent-profiles/{profile_id}/activate")
        assert response.status_code == 200, f"Failed to activate profile: {response.text}"
        print(f"✓ Activated profile")
        
        # Verify it's active
        response = client.get("/api/settings")
        settings = response.json()
        assert settings["active_agent_profile_id"] == profile_id
        print(f"  active_agent_profile_id: {profile_id}")
        
        # Step 3: Set agent_settings to ACP configuration (simulating the bug state)
        print("\n[3] Setting agent_settings to ACP configuration...")
        response = client.patch(
            "/api/settings",
            json={
                "agent_settings_diff": {
                    "agent_kind": "acp",
                    "acp_server": "codex",
                    "acp_model": "gpt-5.5",
                }
            },
        )
        assert response.status_code == 200, f"Failed to set agent_settings: {response.text}"
        print(f"✓ Set agent_settings to ACP")
        
        # Verify agent_settings has ACP configuration
        response = client.get("/api/settings")
        settings = response.json()
        agent_settings = settings["agent_settings"]
        print(f"  agent_kind: {agent_settings['agent_kind']}")
        print(f"  acp_server: {agent_settings.get('acp_server')}")
        print(f"  acp_model: {agent_settings.get('acp_model')}")
        
        assert agent_settings["agent_kind"] == "acp", "agent_settings.agent_kind should be 'acp'"
        assert agent_settings.get("acp_server") == "codex", "agent_settings.acp_server should be 'codex'"
        assert agent_settings.get("acp_model") == "gpt-5.5", "agent_settings.acp_model should be 'gpt-5.5'"
        print("✓ Verified ACP configuration is present (bug state reproduced)")
        
        # Step 4: Delete the ACP profile
        print("\n[4] Deleting active ACP profile...")
        print("    WITHOUT FIX: agent_settings would remain with agent_kind='acp' (BUG)")
        print("    WITH FIX: agent_settings should reset to agent_kind='openhands'")
        response = client.delete("/api/agent-profiles/codex-blackbox-test")
        assert response.status_code == 200, f"Failed to delete profile: {response.text}"
        print(f"✓ Deleted profile")
        
        # Step 5: Verify the fix - agent_settings should be reset to default OpenHands
        print("\n[5] Verifying fix: checking agent_settings after deletion...")
        response = client.get("/api/settings")
        settings_after = response.json()
        agent_settings_after = settings_after["agent_settings"]
        
        print(f"  active_agent_profile_id: {settings_after.get('active_agent_profile_id')}")
        print(f"  agent_kind: {agent_settings_after['agent_kind']}")
        print(f"  acp_server: {agent_settings_after.get('acp_server')}")
        print(f"  acp_model: {agent_settings_after.get('acp_model')}")
        
        # Verify the pointer is cleared
        assert settings_after["active_agent_profile_id"] is None, \
            "active_agent_profile_id should be None after deletion"
        print("✓ active_agent_profile_id cleared")
        
        # Verify agent_settings is reset to default OpenHands
        assert agent_settings_after["agent_kind"] == "openhands", \
            f"agent_settings.agent_kind should be 'openhands', got '{agent_settings_after['agent_kind']}'"
        print("✓ agent_kind reset to 'openhands'")
        
        # Verify ACP-specific fields are cleared
        assert agent_settings_after.get("acp_server") is None, \
            f"agent_settings.acp_server should be None, got '{agent_settings_after.get('acp_server')}'"
        assert agent_settings_after.get("acp_model") is None, \
            f"agent_settings.acp_model should be None, got '{agent_settings_after.get('acp_model')}'"
        print("✓ ACP-specific fields cleared")
        
        print("\n" + "="*70)
        print("✅ BLACK BOX TEST PASSED: Fix for issue #5205 verified!")
        print("="*70)
        print("\nSummary:")
        print("  - ACP profile was created and activated")
        print("  - agent_settings was set to ACP configuration")
        print("  - ACP profile was deleted")
        print("  - agent_settings was properly reset to default OpenHands")
        print("  - No stale ACP configuration remains")
        print()
        
        # Clean up
        reset_stores()


def main():
    """Run the black box integration test."""
    try:
        test_bug_5205()
        print("✅ All tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
