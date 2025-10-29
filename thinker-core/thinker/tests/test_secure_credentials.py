"""
Unit tests for secure credentials.

This module tests the SecureCredentials class including keyring integration,
encrypted file storage, and key management functionality.

Author: Anjan Goswami
"""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from thinker.registry.secure_credentials import SecureCredentials, prompt_for_key


class TestSecureCredentials:
    """Test cases for SecureCredentials class."""
    
    def test_encrypted_file_storage(self):
        """Test encrypted file storage functionality."""
        with tempfile.TemporaryDirectory() as temp_dir:
            keystore_path = os.path.join(temp_dir, "test.keystore")
            creds = SecureCredentials(storage_type="encrypted_file", keystore_path=keystore_path)
            
            # Test setting a key
            assert creds.set("openai", "sk-test-key-123")
            
            # Test getting the key
            retrieved_key = creds.get("openai")
            assert retrieved_key == "sk-test-key-123"
            
            # Test listing providers
            providers = creds.list_providers()
            assert "openai" in providers
            
            # Test deleting the key
            assert creds.delete("openai")
            assert creds.get("openai") is None
    
    def test_encrypted_file_multiple_keys(self):
        """Test storing multiple keys in encrypted file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            keystore_path = os.path.join(temp_dir, "test.keystore")
            creds = SecureCredentials(storage_type="encrypted_file", keystore_path=keystore_path)
            
            # Set multiple keys
            assert creds.set("openai", "sk-openai-key")
            assert creds.set("anthropic", "sk-anthropic-key")
            assert creds.set("google", "sk-google-key")
            
            # Verify all keys
            assert creds.get("openai") == "sk-openai-key"
            assert creds.get("anthropic") == "sk-anthropic-key"
            assert creds.get("google") == "sk-google-key"
            
            # Verify provider listing
            providers = creds.list_providers()
            assert set(providers) == {"openai", "anthropic", "google"}
    
    def test_encrypted_file_empty_key(self):
        """Test handling of empty keys."""
        with tempfile.TemporaryDirectory() as temp_dir:
            keystore_path = os.path.join(temp_dir, "test.keystore")
            creds = SecureCredentials(storage_type="encrypted_file", keystore_path=keystore_path)
            
            # Empty key should fail
            assert not creds.set("openai", "")
            assert not creds.set("openai", "   ")
            assert not creds.set("openai", None)
    
    def test_encrypted_file_corrupted_data(self):
        """Test handling of corrupted encrypted data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            keystore_path = os.path.join(temp_dir, "test.keystore")
            creds = SecureCredentials(storage_type="encrypted_file", keystore_path=keystore_path)
            
            # Write corrupted data
            Path(keystore_path).write_bytes(b"corrupted data")
            
            # Should handle gracefully
            assert creds.get("openai") is None
            assert creds.list_providers() == []
    
    @patch('keyring.get_password')
    @patch('keyring.set_password')
    def test_keyring_storage(self, mock_set, mock_get):
        """Test keyring storage functionality."""
        mock_get.return_value = "sk-keyring-key"
        mock_set.return_value = None
        
        creds = SecureCredentials(storage_type="keyring")
        
        # Test getting key
        key = creds.get("openai")
        assert key == "sk-keyring-key"
        mock_get.assert_called_with("thinker-core", "openai")
        
        # Test setting key
        assert creds.set("openai", "sk-new-key")
        mock_set.assert_called_with("thinker-core", "openai", "sk-new-key")
    
    @patch('keyring.get_password')
    @patch('keyring.delete_password')
    def test_keyring_delete(self, mock_delete, mock_get):
        """Test keyring delete functionality."""
        mock_get.return_value = None
        mock_delete.return_value = None
        
        creds = SecureCredentials(storage_type="keyring")
        
        # Test delete
        assert creds.delete("openai")
        mock_delete.assert_called_with("thinker-core", "openai")
    
    @patch('keyring.get_password')
    def test_keyring_list_providers(self, mock_get):
        """Test keyring provider listing."""
        def side_effect(service, provider):
            return "sk-key" if provider in ["openai", "anthropic"] else None
        
        mock_get.side_effect = side_effect
        
        creds = SecureCredentials(storage_type="keyring")
        providers = creds.list_providers()
        
        # Should find providers with keys
        assert "openai" in providers
        assert "anthropic" in providers
    
    def test_environment_fallback(self):
        """Test environment variable fallback."""
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-env-key'}):
            creds = SecureCredentials(storage_type="env")
            key = creds.get("openai")
            assert key == "sk-env-key"
    
    def test_auto_storage_priority(self):
        """Test auto storage type priority (keyring > encrypted_file > env)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            keystore_path = os.path.join(temp_dir, "test.keystore")
            creds = SecureCredentials(storage_type="encrypted_file", keystore_path=keystore_path)
            
            # Test encrypted file storage directly
            assert creds.set("openai", "sk-auto-key")
            assert creds.get("openai") == "sk-auto-key"
    
    def test_test_key_functionality(self):
        """Test key testing functionality."""
        with tempfile.TemporaryDirectory() as temp_dir:
            keystore_path = os.path.join(temp_dir, "test.keystore")
            creds = SecureCredentials(storage_type="encrypted_file", keystore_path=keystore_path)
            
            # Test with invalid key
            creds.set("openai", "sk-invalid-key")
            success, message = creds.test_key("openai")
            assert not success
            assert "invalid" in message.lower() or "error" in message.lower()
    
    def test_prompt_for_key(self):
        """Test key prompting functionality."""
        with patch('getpass.getpass') as mock_getpass:
            mock_getpass.side_effect = ["test-key-123", "test-key-123"]
            
            key = prompt_for_key("openai")
            assert key == "test-key-123"
            assert mock_getpass.call_count == 2
    
    def test_prompt_for_key_mismatch(self):
        """Test key prompting with mismatched keys."""
        with patch('getpass.getpass') as mock_getpass:
            mock_getpass.side_effect = ["test-key-123", "different-key"]
            
            with pytest.raises(ValueError, match="Keys do not match"):
                prompt_for_key("openai")
    
    def test_prompt_for_key_empty(self):
        """Test key prompting with empty key."""
        with patch('getpass.getpass') as mock_getpass:
            mock_getpass.side_effect = ["", ""]
            
            with pytest.raises(ValueError, match="Key cannot be empty"):
                prompt_for_key("openai")
