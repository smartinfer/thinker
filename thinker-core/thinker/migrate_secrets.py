"""
Migration script for Thinker Core secrets.

This script helps users migrate from plaintext JSON secrets files
to secure credential storage (keyring or encrypted file).

Author: Anjan Goswami
"""

import json
import getpass
from pathlib import Path
from .registry.secure_credentials import SecureCredentials


def migrate_secrets(secrets_path: str, storage_type: str = "auto") -> bool:
    """
    Migrate secrets from plaintext JSON to secure storage.
    
    Args:
        secrets_path: Path to the plaintext secrets.json file
        storage_type: Storage type ("keyring", "encrypted_file", or "auto")
        
    Returns:
        True if migration successful, False otherwise
    """
    secrets_file = Path(secrets_path)
    if not secrets_file.exists():
        print(f"❌ Secrets file not found: {secrets_path}")
        return False
    
    try:
        # Load plaintext secrets
        with open(secrets_file) as f:
            secrets = json.load(f)
        
        if not secrets:
            print("ℹ️  No secrets found in file")
            return True
        
        print(f"📁 Found {len(secrets)} providers in {secrets_path}")
        
        # Initialize secure credentials
        creds = SecureCredentials(storage_type=storage_type)
        
        # Migrate each provider
        migrated = 0
        failed = 0
        
        for provider, provider_data in secrets.items():
            if isinstance(provider_data, dict) and "api_key" in provider_data:
                key = provider_data["api_key"]
            elif isinstance(provider_data, str):
                key = provider_data
            else:
                print(f"⚠️  Skipping {provider}: invalid format")
                failed += 1
                continue
            
            if not key or not key.strip():
                print(f"⚠️  Skipping {provider}: empty key")
                failed += 1
                continue
            
            try:
                if creds.set(provider, key.strip()):
                    print(f"✅ Migrated {provider}")
                    migrated += 1
                else:
                    print(f"❌ Failed to migrate {provider}")
                    failed += 1
            except Exception as e:
                print(f"❌ Error migrating {provider}: {e}")
                failed += 1
        
        print(f"\n📊 Migration complete:")
        print(f"  ✅ Migrated: {migrated}")
        print(f"  ❌ Failed: {failed}")
        
        if migrated > 0:
            print(f"\n🔒 Keys are now stored securely using {storage_type} storage")
            print("💡 You can safely delete the plaintext file:")
            print(f"   rm {secrets_path}")
        
        return failed == 0
        
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in secrets file: {e}")
        return False
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False


def main():
    """Main migration entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Migrate Thinker secrets to secure storage")
    parser.add_argument("secrets_file", help="Path to plaintext secrets.json file")
    parser.add_argument("--storage", choices=["keyring", "encrypted_file", "auto"], 
                       default="auto", help="Storage type to use")
    
    args = parser.parse_args()
    
    print("🔐 Thinker Secrets Migration")
    print("=" * 40)
    
    success = migrate_secrets(args.secrets_file, args.storage)
    
    if success:
        print("\n🎉 Migration completed successfully!")
    else:
        print("\n💥 Migration completed with errors")
        exit(1)


if __name__ == "__main__":
    main()
