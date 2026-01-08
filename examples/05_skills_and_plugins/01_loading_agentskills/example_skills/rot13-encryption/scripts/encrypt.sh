#!/bin/bash
# ROT13 encryption - encrypts/decrypts the input message
echo "$1" | tr 'A-Za-z' 'N-ZA-Mn-za-m'
