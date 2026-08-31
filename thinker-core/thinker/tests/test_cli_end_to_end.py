"""
End-to-end tests for CLI functionality.

This module tests the complete CLI workflow including chat-ql and map-chat-ql
commands with real ThinkerQL files.

Author: Anjan Goswami
"""

import pytest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, Mock
from thinker.cli import main
import sys
from io import StringIO

@pytest.fixture
def temp_registry_file():
    """Create a temporary registry file."""
    registry_data = {
        "version": "1",
        "calls": [
            {
                "call_id": "local:echo.chat",
                "provider": "local",
                "model_id": "echo",
                "kind": "chat",
                "modality": "text",
                "caps": ["json_mode"],
                "limits": {"max_input_tokens": 8192, "max_output_tokens": 1024},
                "price": {"input_per_1k": 0.0, "output_per_1k": 0.0},
                "adapter": "local",
                "payload_style": "echo"
            }
        ]
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        import yaml
        yaml.dump(registry_data, f)
        return f.name

@pytest.fixture
def temp_pricebook_file():
    """Create a temporary pricebook file."""
    pricebook_data = {}
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(pricebook_data, f)
        return f.name

@pytest.fixture
def temp_ql_file():
    """Create a temporary ThinkerQL file."""
    ql_data = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Hello from CLI test!"}]
            }
        ],
        "routing": {"call_id": "local:echo.chat"}
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        import yaml
        yaml.dump(ql_data, f)
        return f.name

def test_cli_chat_ql_command(temp_registry_file, temp_ql_file):
    """Test chat-ql CLI command."""
    with patch('sys.argv', [
        'thinker', 'chat-ql',
        '--registry', temp_registry_file,
        '--ql', temp_ql_file
    ]):
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            with patch('thinker.core.Thinker.from_files') as mock_from_files:
                # Mock Thinker instance
                mock_thinker = Mock()
                mock_response = Mock()
                mock_response.text = "echo:Hello from CLI test!"
                mock_response.model = "echo"
                mock_response.provider = "local"
                mock_response.tokens = {"input": 5, "output": 5}
                mock_response.cost_usd = 0.0
                mock_thinker.chat_ql.return_value = mock_response
                mock_from_files.return_value = mock_thinker
                
                main()
                
                # Verify output
                output = mock_stdout.getvalue()
                assert "echo:Hello from CLI test!" in output
                assert "echo" in output
                assert "local" in output

def test_cli_map_chat_ql_command(temp_registry_file):
    """Test map-chat-ql CLI command."""
    # Create multiple QL files
    ql_files = []
    for i in range(3):
        ql_data = {
            "version": "0.3",
            "intent": "chat",
            "messages": [
                {
                    "role": "user",
                    "parts": [{"type": "text", "text": f"Message {i}"}]
                }
            ],
            "routing": {"call_id": "local:echo.chat"}
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            import yaml
            yaml.dump(ql_data, f)
            ql_files.append(f.name)
    
    try:
        with patch('sys.argv', [
            'thinker', 'map-chat-ql',
            '--registry', temp_registry_file,
            '--ql-glob', '*.yaml',
            '--budget-usd', '0.05'
        ]):
            with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
                with patch('thinker.core.Thinker.from_files') as mock_from_files:
                    with patch('glob.glob', return_value=ql_files):
                        # Mock Thinker instance
                        mock_thinker = Mock()
                        mock_responses = [
                            Mock(text=f"echo:Message {i}", tokens={"input": 2, "output": 2}, cost_usd=0.0)
                            for i in range(3)
                        ]
                        mock_thinker.chat_ql.side_effect = mock_responses
                        mock_from_files.return_value = mock_thinker
                        
                        main()
                        
                        # Verify output
                        output = mock_stdout.getvalue()
                        assert "echo:Message 0" in output
                        assert "echo:Message 1" in output
                        assert "echo:Message 2" in output
                        
    finally:
        # Clean up temporary files
        for f in ql_files:
            Path(f).unlink()

def test_cli_registry_validate_command(temp_registry_file):
    """Test registry-validate CLI command."""
    with patch('sys.argv', [
        'thinker', 'registry-validate',
        temp_registry_file
    ]):
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            main()
            
            # Verify output
            output = mock_stdout.getvalue()
            assert "OK" in output
            assert temp_registry_file in output
            assert "sha256=" in output
            assert "calls=1" in output

def test_cli_registry_load_command(temp_registry_file):
    """Test registry-load CLI command."""
    with patch('sys.argv', [
        'thinker', 'registry-load',
        temp_registry_file
    ]):
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            main()
            
            # Verify output
            output = mock_stdout.getvalue()
            assert "version" in output
            assert "calls" in output
            assert "1" in output  # 1 call in registry

def test_cli_registry_list_command(temp_registry_file):
    """Test registry-list CLI command."""
    # This test is complex because registry-list needs a loaded store
    # For now, just test that it runs without error
    with patch('sys.argv', [
        'thinker', 'registry-list'
    ]):
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            main()

            # Verify it runs (empty output is expected since no registry is loaded)
            output = mock_stdout.getvalue()
            # Should be empty since no registry is loaded
            assert output == ""

def test_cli_keys_rotate_preserves_original_when_candidate_fails():
    """A failed candidate key must never replace the stored key."""
    class FakeCredentials:
        def __init__(self):
            self.keys = {"openai": "original-value"}

        def test_key(self, provider, key=None):
            if key == "candidate-value":
                return False, "candidate is invalid"
            return True, "current key is valid"

        def set(self, provider, key):
            self.keys[provider] = key
            return True

    creds = FakeCredentials()
    config = Mock(credentials_storage_type="auto", keystore_path="/tmp/test-keystore")
    with patch("sys.argv", ["thinker", "keys", "rotate"]), \
         patch("builtins.input", return_value="openai"), \
         patch("thinker.cli.prompt_for_key", return_value="candidate-value"), \
         patch("thinker.cli.get_config", return_value=config), \
         patch("thinker.cli.SecureCredentials", return_value=creds):
        main()

    assert creds.keys["openai"] == "original-value"

def test_cli_keys_honors_configured_credential_storage():
    """CLI credential commands use the configured storage backend and path."""
    config = Mock(credentials_storage_type="encrypted_file", keystore_path="/tmp/test-keystore")
    creds = Mock()
    creds.list_providers.return_value = []
    with patch("sys.argv", ["thinker", "keys", "list"]), \
         patch("thinker.cli.get_config", return_value=config), \
         patch("thinker.cli.SecureCredentials", return_value=creds) as credentials_class:
        main()

    credentials_class.assert_called_once_with(
        storage_type="encrypted_file",
        keystore_path="/tmp/test-keystore",
    )
