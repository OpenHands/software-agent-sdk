#!/usr/bin/env python3
"""
Simple example app demonstrating REST API functions.

This module provides a simple HTTP server with REST API endpoints.

REST API functions are now in rest_api.py (refactored from TODO).
"""

from rest_api import get_health, get_status, get_version, create_item, get_items


def main():
    """Main application entry point."""
    print("REST API Application")
    print(f"Health: {get_health()}")
    print(f"Status: {get_status()}")
    print(f"Version: {get_version()}")
    
    # Demo CRUD operations
    items = get_items()
    print(f"Current items: {items}")
    
    new_item = create_item("test_item", {"value": 42})
    print(f"Created item: {new_item}")
    
    items = get_items()
    print(f"Updated items: {items}")


if __name__ == "__main__":
    main()
