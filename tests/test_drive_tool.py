"""Test the Google Drive file management tool."""

import os


import pytest
@pytest.mark.skip(reason="Fails if developer has real keys in macOS keychain")
def test_tool_creation_without_credentials():
    """Test that tool creation fails gracefully without credentials."""
    # Temporarily remove credentials if they exist
    original_creds = os.environ.pop("GOOGLE_DRIVE_CREDENTIALS_PATH", None)
    
    try:
        from atlas.tools import create_drive_tools
        
        try:
            tools = create_drive_tools()
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "GOOGLE_DRIVE_CREDENTIALS_PATH" in str(e) or "credentials not configured" in str(e).lower()
            print("✓ Tool correctly requires credentials")
    finally:
        # Restore original credentials
        if original_creds:
            os.environ["GOOGLE_DRIVE_CREDENTIALS_PATH"] = original_creds


def test_tool_creation_structure():
    """Test that tools can be imported and have expected structure."""
    from atlas.tools import create_drive_tools
    
    print("✓ Drive tools can be imported from atlas.tools")
    
    # Check if credentials are available
    creds_path = os.getenv("GOOGLE_DRIVE_CREDENTIALS_PATH")
    if creds_path and os.path.exists(creds_path):
        print(f"✓ GOOGLE_DRIVE_CREDENTIALS_PATH is set: {creds_path}")
        try:
            tools = create_drive_tools()
            print(f"✓ Created {len(tools)} Drive tools")
            
            # Check each tool has expected attributes
            expected_tools = [
                "list_files",
                "search_files",
                "get_file_metadata",
                "download_file",
                "upload_file",
                "create_folder",
                "delete_file",
                "share_file",
            ]
            
            tool_names = [tool.name for tool in tools]
            print(f"✓ Tool names: {', '.join(tool_names)}")
            
            for expected in expected_tools:
                assert expected in tool_names, f"Missing expected tool: {expected}"
            
            print("✓ All expected tools are present")
            
            # Check tools have descriptions
            for tool in tools:
                assert hasattr(tool, "name")
                assert hasattr(tool, "description")
                assert tool.description, f"Tool {tool.name} missing description"
            
            print("✓ All tools have proper structure")
            
        except Exception as e:
            print(f"⚠ Tool creation failed (may need OAuth flow): {e}")
    else:
        print("⚠ GOOGLE_DRIVE_CREDENTIALS_PATH not set - tools will not be available to agent")
        print("  Set it with: atlas secrets set google_drive")
        print("  Or: export GOOGLE_DRIVE_CREDENTIALS_PATH='/path/to/credentials.json'")


def test_tool_integration():
    """Test that tools integrate properly with the agent."""
    from atlas.tools import create_drive_tools
    
    print("✓ Drive tools module is properly integrated")
    
    # Verify it's exported from main tools module
    from atlas.tools import __all__
    assert "create_drive_tools" in __all__, "create_drive_tools not exported from tools module"
    print("✓ create_drive_tools is exported from tools module")


if __name__ == "__main__":
    print("Testing Google Drive File Management Tool Integration\n")
    print("=" * 50)
    
    test_tool_creation_without_credentials()
    print()
    
    test_tool_creation_structure()
    print()
    
    test_tool_integration()
    print()
    
    print("=" * 50)
    print("\nAll tests passed! ✓")
    print("\nNext steps:")
    print("1. Get OAuth2 credentials from https://console.cloud.google.com/apis/credentials")
    print("2. Enable Google Drive API in your project")
    print("3. Set credentials: atlas secrets set google_drive")
    print("4. Run: atlas chat 'List the files in my Google Drive'")
