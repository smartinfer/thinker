# Security Guide

This document describes the security features and best practices for Thinker Core.

## Secure Credential Management

Thinker Core provides multiple secure storage options for API keys and credentials:

### Storage Options

1. **OS Keyring (Recommended)**
   - Uses the system's native credential manager
   - Windows: Windows Credential Manager
   - macOS: Keychain
   - Linux: Secret Service (GNOME Keyring, KDE Wallet, etc.)
   - Most secure option as keys are managed by the OS

2. **Encrypted File Storage**
   - Uses Fernet symmetric encryption (AES 128 in CBC mode)
   - Master key stored in `~/.thinker/master.key` (600 permissions)
   - Encrypted keystore at `~/.thinker/keystore.encrypted` (600 permissions)
   - Fallback when keyring is not available

3. **Environment Variables**
   - Traditional approach using `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.
   - Least secure but useful for CI/CD environments
   - Used as final fallback

### Key Management Commands

```bash
# Set a new API key
thinker keys set

# List configured providers
thinker keys list

# Test an API key
thinker keys test

# Rotate an API key
thinker keys rotate

# Delete an API key
thinker keys delete
```

### Migration from Plaintext

If you have existing plaintext secrets files, use the migration script:

```bash
python -m thinker.migrate_secrets secrets.json --storage auto
```

This will:
1. Read your existing `secrets.json` file
2. Prompt for confirmation
3. Store keys securely using the specified storage type
4. Verify the migration was successful

## Security Best Practices

### 1. Never Store Keys in Code

❌ **Don't do this:**
```python
# BAD - Never hardcode API keys
api_key = "sk-1234567890abcdef"
```

✅ **Do this:**
```python
# GOOD - Use secure credential management
from thinker.registry.auth import get_credentials
creds = get_credentials()
api_key = creds.get("openai")
```

### 2. Use Environment Variables for CI/CD

For automated environments where keyring isn't available:

```bash
export OPENAI_API_KEY="sk-your-key-here"
export ANTHROPIC_API_KEY="sk-your-key-here"
```

### 3. Regular Key Rotation

Rotate your API keys regularly:

```bash
# Test current key
thinker keys test openai

# Rotate to new key
thinker keys rotate openai
```

### 4. Monitor Key Usage

Use the observability features to monitor key usage:

```bash
# View usage statistics
thinker stats

# Live monitoring
thinker stats --live
```

## File Permissions

The secure storage system sets restrictive file permissions:

- `~/.thinker/` directory: 700 (owner read/write/execute only)
- `~/.thinker/master.key`: 600 (owner read/write only)
- `~/.thinker/keystore.encrypted`: 600 (owner read/write only)
- `~/.thinker/metrics.db`: 600 (owner read/write only)

## Audit Logging

All key operations are logged for security auditing:

- Key storage attempts (success/failure)
- Key retrieval attempts
- Key deletion operations
- Failed authentication attempts

Logs are stored in `~/.thinker/audit.log` with timestamps and operation details.

## Threat Model

### What Thinker Protects Against

1. **Accidental Key Exposure**
   - Keys never appear in logs or error messages
   - Keys are redacted in debug output
   - Secure storage prevents accidental file sharing

2. **Local System Compromise**
   - OS keyring provides additional protection
   - Encrypted files require master key access
   - File permissions prevent unauthorized access

3. **Code Repository Exposure**
   - Keys never stored in version control
   - `.gitignore` prevents accidental commits
   - Migration tools help clean up plaintext files

### What Thinker Doesn't Protect Against

1. **Root/Admin Access**
   - System administrators can access all files
   - OS keyring can be accessed with admin privileges
   - Use additional encryption for highly sensitive environments

2. **Memory Dumps**
   - Keys exist in memory during processing
   - Consider using secure memory libraries for high-security use cases

3. **Network Interception**
   - API calls are made over HTTPS
   - Consider VPN or additional network security for sensitive data

## Compliance

### GDPR
- No personal data is stored in credentials
- Keys can be deleted using `thinker keys delete`
- Audit logs can be purged with `thinker stats --cleanup`

### SOC 2
- Secure credential storage meets SOC 2 requirements
- Audit logging provides necessary compliance data
- File permissions follow security best practices

### HIPAA
- No PHI is stored in the credential system
- Consider additional encryption for healthcare environments
- Regular key rotation recommended

## Troubleshooting

### Keyring Not Available

If you get keyring errors:

```bash
# Use encrypted file storage instead
export THINKER_CREDENTIALS_STORAGE=encrypted_file
thinker keys set
```

### Permission Denied

If you get permission errors:

```bash
# Fix directory permissions
chmod 700 ~/.thinker
chmod 600 ~/.thinker/*

# Or recreate the directory
rm -rf ~/.thinker
thinker keys set
```

### Migration Issues

If migration fails:

```bash
# Check file format
cat secrets.json | jq .

# Try manual migration
thinker keys set
# Enter each key manually
```

## Support

For security-related issues:

1. Check the audit logs: `~/.thinker/audit.log`
2. Verify file permissions
3. Test with a simple key: `thinker keys test openai`
4. Report issues with sanitized logs (no keys included)
