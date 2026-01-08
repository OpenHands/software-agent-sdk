---
name: rot13-encryption
description: >
  This skill helps encrypt and decrypt messages using ROT13 cipher.
  Use when the user asks to "encrypt" or "decrypt" a message.
license: MIT
compatibility: Requires bash
metadata:
  author: openhands
  version: "1.0"
triggers:
  - encrypt
  - decrypt
  - cipher
---

# ROT13 Encryption Skill

This skill provides a script for encrypting messages using ROT13.

## How to Encrypt

Run the [encrypt.sh](scripts/encrypt.sh) script with your message:

```bash
./scripts/encrypt.sh "your message"
```

## Examples

See [examples.md](references/examples.md) for more usage examples.
