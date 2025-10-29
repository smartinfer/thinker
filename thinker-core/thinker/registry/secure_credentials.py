"""
Secure credential management for Thinker Core.

This module provides secure storage for API keys using OS keyring integration
and encrypted file storage as fallback. Never stores keys in plaintext.

Author: Anjan Goswami
"""

import os
import json
import getpass
from pathlib import Path
from typing import Optional, Dict, Any
from cryptography.fernet import Fernet
import keyring
import keyring.errors


class SecureCredentials:
    """Manages API credentials with secure storage options."""
    
    def __init__(self, storage_type: str = "keyring", keystore_path: Optional[str] = None):
        """
        Initialize secure credentials manager.
        
        Args:
            storage_type: "keyring", "encrypted_file", or "env"
            keystore_path: Path for encrypted file storage (default: ~/.thinker/keystore.encrypted)
        """
        self.storage_type = storage_type
        self.keystore_path = keystore_path or str(Path.home() / ".thinker" / "keystore.encrypted")
        self._service_name = "thinker-core"
        self._key_file = Path(self.keystore_path).parent / "master.key"
        
        # Ensure .thinker directory exists
        Path(self.keystore_path).parent.mkdir(parents=True, exist_ok=True)
    
    def get(self, provider: str) -> Optional[str]:
        """
        Get API key for provider from secure storage.
        
        Priority: keyring > encrypted file > environment variables
        
        Args:
            provider: Provider name (openai, anthropic, etc.)
            
        Returns:
            API key or None if not found
        """
        # Try keyring first
        if self.storage_type in ["keyring", "auto"]:
            try:
                key = keyring.get_password(self._service_name, provider)
                if key:
                    return key
            except keyring.errors.KeyringError:
                pass
        
        # Try encrypted file
        if self.storage_type in ["encrypted_file", "auto"]:
            try:
                return self._get_from_encrypted_file(provider)
            except Exception:
                pass
        
        # Fallback to environment variables
        env_vars = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "together": "TOGETHER_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "google": "GOOGLE_API_KEY"
        }
        
        env_var = env_vars.get(provider)
        if env_var:
            return os.getenv(env_var)
        
        return None
    
    def set(self, provider: str, key: str) -> bool:
        """
        Store API key securely.
        
        Args:
            provider: Provider name
            key: API key to store
            
        Returns:
            True if successful, False otherwise
        """
        if not key or not key.strip():
            return False
        
        key = key.strip()
        
        # Try keyring first
        if self.storage_type in ["keyring", "auto"]:
            try:
                keyring.set_password(self._service_name, provider, key)
                return True
            except keyring.errors.KeyringError:
                pass
        
        # Fallback to encrypted file
        if self.storage_type in ["encrypted_file", "auto"]:
            try:
                return self._set_in_encrypted_file(provider, key)
            except Exception:
                pass
        
        return False
    
    def delete(self, provider: str) -> bool:
        """
        Delete API key from storage.
        
        Args:
            provider: Provider name
            
        Returns:
            True if successful, False otherwise
        """
        # Try keyring first
        if self.storage_type in ["keyring", "auto"]:
            try:
                keyring.delete_password(self._service_name, provider)
                return True
            except keyring.errors.PasswordDeleteError:
                pass
        
        # Try encrypted file
        if self.storage_type in ["encrypted_file", "auto"]:
            try:
                return self._delete_from_encrypted_file(provider)
            except Exception:
                pass
        
        return False
    
    def list_providers(self) -> list[str]:
        """
        List providers with stored keys.
        
        Returns:
            List of provider names
        """
        providers = []
        
        # Check keyring
        if self.storage_type in ["keyring", "auto"]:
            try:
                # Note: keyring doesn't have a list_passwords method
                # We'll check common providers
                common_providers = ["openai", "anthropic", "together", "mistral", "google"]
                for provider in common_providers:
                    if keyring.get_password(self._service_name, provider):
                        providers.append(provider)
            except keyring.errors.KeyringError:
                pass
        
        # Check encrypted file
        if self.storage_type in ["encrypted_file", "auto"]:
            try:
                file_providers = self._list_from_encrypted_file()
                providers.extend(file_providers)
            except Exception:
                pass
        
        # Remove duplicates and sort
        return sorted(list(set(providers)))
    
    def test_key(self, provider: str) -> tuple[bool, str]:
        """
        Test if a stored key works by making a test API call.
        
        Args:
            provider: Provider name
            
        Returns:
            (success, message) tuple
        """
        key = self.get(provider)
        if not key:
            return False, f"No key found for provider: {provider}"
        
        # Import here to avoid circular imports
        from .auth import _test_provider_key
        
        try:
            success, message = _test_provider_key(provider, key)
            return success, message
        except Exception as e:
            return False, f"Test failed: {str(e)}"
    
    def _get_encryption_key(self) -> bytes:
        """Get or create encryption key for file storage."""
        if self._key_file.exists():
            return self._key_file.read_bytes()
        else:
            key = Fernet.generate_key()
            self._key_file.write_bytes(key)
            # Set restrictive permissions
            self._key_file.chmod(0o600)
            return key
    
    def _get_from_encrypted_file(self, provider: str) -> Optional[str]:
        """Get key from encrypted file."""
        if not Path(self.keystore_path).exists():
            return None
        
        try:
            key = self._get_encryption_key()
            fernet = Fernet(key)
            
            encrypted_data = Path(self.keystore_path).read_bytes()
            decrypted_data = fernet.decrypt(encrypted_data)
            keystore = json.loads(decrypted_data.decode())
            
            return keystore.get(provider)
        except Exception:
            return None
    
    def _set_in_encrypted_file(self, provider: str, api_key: str) -> bool:
        """Store key in encrypted file."""
        try:
            key = self._get_encryption_key()
            fernet = Fernet(key)
            
            # Load existing keystore or create new one
            keystore = {}
            if Path(self.keystore_path).exists():
                try:
                    encrypted_data = Path(self.keystore_path).read_bytes()
                    decrypted_data = fernet.decrypt(encrypted_data)
                    keystore = json.loads(decrypted_data.decode())
                except Exception:
                    # If decryption fails, start fresh
                    keystore = {}
            
            # Update keystore
            keystore[provider] = api_key
            
            # Encrypt and save
            encrypted_data = fernet.encrypt(json.dumps(keystore).encode())
            Path(self.keystore_path).write_bytes(encrypted_data)
            
            # Set restrictive permissions
            Path(self.keystore_path).chmod(0o600)
            
            return True
        except Exception:
            return False
    
    def _delete_from_encrypted_file(self, provider: str) -> bool:
        """Delete key from encrypted file."""
        if not Path(self.keystore_path).exists():
            return True
        
        try:
            key = self._get_encryption_key()
            fernet = Fernet(key)
            
            encrypted_data = Path(self.keystore_path).read_bytes()
            decrypted_data = fernet.decrypt(encrypted_data)
            keystore = json.loads(decrypted_data.decode())
            
            if provider in keystore:
                del keystore[provider]
                
                if keystore:
                    # Save updated keystore
                    encrypted_data = fernet.encrypt(json.dumps(keystore).encode())
                    Path(self.keystore_path).write_bytes(encrypted_data)
                else:
                    # Delete file if empty
                    Path(self.keystore_path).unlink()
            
            return True
        except Exception:
            return False
    
    def _list_from_encrypted_file(self) -> list[str]:
        """List providers from encrypted file."""
        if not Path(self.keystore_path).exists():
            return []
        
        try:
            key = self._get_encryption_key()
            fernet = Fernet(key)
            
            encrypted_data = Path(self.keystore_path).read_bytes()
            decrypted_data = fernet.decrypt(encrypted_data)
            keystore = json.loads(decrypted_data.decode())
            
            return list(keystore.keys())
        except Exception:
            return []


def prompt_for_key(provider: str) -> str:
    """Prompt user for API key with confirmation."""
    print(f"Enter API key for {provider}:")
    key1 = getpass.getpass("Key: ")
    key2 = getpass.getpass("Confirm key: ")
    
    if key1 != key2:
        raise ValueError("Keys do not match")
    
    if not key1.strip():
        raise ValueError("Key cannot be empty")
    
    return key1.strip()
