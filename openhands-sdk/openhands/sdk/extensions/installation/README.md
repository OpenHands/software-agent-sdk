# Installation

This module provides utilities for installing, tracking, and loading extensions.

## How to Use

The main entry point is `InstalledExtensionManager`. When constructing the manager, we must provide an installation directory and an installation interface. The latter provides methods to load an extension from disk and to generate installation metadata.

EXAMPLE SOURCE GOES HERE

Once the `InstalledExtensionManager` is instantiated, you can install/uninstall extensions, enable/disable installed extensions, and get information about the currently-managed extensions.
