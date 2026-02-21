"""
REST API functions for the application.

This module contains all REST API-related functions that were extracted
from app.py for better code organization and maintainability.
"""

# In-memory storage for demo purposes
_items: dict = {}


def get_health() -> dict:
    """Get health status of the API.
    
    Returns:
        dict: Health status information.
    """
    return {"status": "healthy", "service": "rest-api"}


def get_status() -> dict:
    """Get current status of the API server.
    
    Returns:
        dict: Server status information.
    """
    return {
        "running": True,
        "uptime": "unknown",
        "connections": 0,
    }


def get_version() -> dict:
    """Get API version information.
    
    Returns:
        dict: Version information.
    """
    return {
        "version": "1.0.0",
        "api_version": "v1",
    }


def create_item(name: str, data: dict) -> dict:
    """Create a new item in the API.
    
    Args:
        name: Name/identifier for the item.
        data: Data to store for the item.
        
    Returns:
        dict: Created item information.
    """
    _items[name] = data
    return {"name": name, "data": data, "created": True}


def get_items() -> dict:
    """Get all items from the API.
    
    Returns:
        dict: All stored items.
    """
    return dict(_items)


def get_item(name: str) -> dict | None:
    """Get a specific item by name.
    
    Args:
        name: Name of the item to retrieve.
        
    Returns:
        dict | None: Item data if found, None otherwise.
    """
    return _items.get(name)


def delete_item(name: str) -> bool:
    """Delete an item by name.
    
    Args:
        name: Name of the item to delete.
        
    Returns:
        bool: True if deleted, False if not found.
    """
    if name in _items:
        del _items[name]
        return True
    return False
