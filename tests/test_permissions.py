"""Tests for permission management improvements."""

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from cryptography.fernet import Fernet

# Generate a valid Fernet key for all tests
_TEST_KEY = Fernet.generate_key()


def _make_test_pm(**kwargs):
    """Create a PermissionManager suitable for testing (no real keyring/disk)."""
    from atlas.security.permissions import PermissionManager
    
    with patch.object(PermissionManager, '_get_key', return_value=_TEST_KEY):
        with patch.object(PermissionManager, '_load_grants'):
            with patch.object(PermissionManager, '_save_grants'):
                pm = PermissionManager(**kwargs)
                return pm


# --- Preset Tests ---

class TestPermissionPresets:
    """Test permission preset functionality."""
    
    def test_presets_exist(self):
        """All four presets are defined."""
        from atlas.security.permissions import PermissionManager
        
        presets = PermissionManager.PERMISSION_PRESETS
        assert "minimal" in presets
        assert "reader" in presets
        assert "standard" in presets
        assert "full" in presets
    
    def test_minimal_preset_is_empty(self):
        """Minimal preset grants no permissions."""
        from atlas.security.permissions import PermissionManager
        assert PermissionManager.PERMISSION_PRESETS["minimal"] == []
    
    def test_preset_hierarchy(self):
        """Each preset is a superset of the previous one."""
        from atlas.security.permissions import PermissionManager
        presets = PermissionManager.PERMISSION_PRESETS
        
        minimal = set(presets["minimal"])
        reader = set(presets["reader"])
        standard = set(presets["standard"])
        full = set(presets["full"])
        
        assert minimal.issubset(reader)
        assert reader.issubset(standard)
        assert standard.issubset(full)
    
    def test_apply_preset(self):
        """Applying a preset creates grants for all its permissions."""
        from atlas.security.permissions import PermissionManager
        
        pm = _make_test_pm()
        asyncio.run(pm.apply_preset("reader"))
        
        assert pm.get_active_preset() == "reader"
        expected_perms = PermissionManager.PERMISSION_PRESETS["reader"]
        for perm in expected_perms:
            assert any(g.permission == perm and g.scope == "*" for g in pm.grants), f"Missing grant for {perm}"
            grant = next(g for g in pm.grants if g.permission == perm and g.scope == "*")
            assert grant.granted is True
    
    def test_apply_invalid_preset_raises(self):
        """Applying an unknown preset raises ValueError."""
        pm = _make_test_pm()
        
        with pytest.raises(ValueError, match="Unknown preset"):
            asyncio.run(pm.apply_preset("nonexistent"))


# --- Wildcard Matching Tests ---

class TestWildcardMatching:
    """Test fnmatch-based wildcard scope matching."""
    
    def test_exact_match(self):
        """Exact permission:scope match works."""
        from atlas.security.permissions import PermissionGrant
        
        pm = _make_test_pm()
        pm.grants.append(PermissionGrant(
            permission="email_read",
            scope="gmail.com",
            granted=True,
            granted_at=datetime.now(),
            expires_at=None,
        ))
        
        result = asyncio.run(pm.check("email_read", "gmail.com"))
        assert result is True
    
    def test_wildcard_permission(self):
        """Wildcard in permission name matches (e.g. calendar_* matches calendar_read)."""
        from atlas.security.permissions import PermissionGrant
        
        pm = _make_test_pm()
        pm.grants.append(PermissionGrant(
            permission="calendar_*",
            scope="*",
            granted=True,
            granted_at=datetime.now(),
            expires_at=None,
        ))
        
        result = asyncio.run(pm.check("calendar_read", "calendar.google.com"))
        assert result is True
        
        result = asyncio.run(pm.check("calendar_write", "calendar.google.com"))
        assert result is True
    
    def test_wildcard_scope(self):
        """Wildcard in scope matches (e.g. *.google.com matches calendar.google.com)."""
        from atlas.security.permissions import PermissionGrant
        
        pm = _make_test_pm()
        pm.grants.append(PermissionGrant(
            permission="email_read",
            scope="*.google.com",
            granted=True,
            granted_at=datetime.now(),
            expires_at=None,
        ))
        
        result = asyncio.run(pm.check("email_read", "mail.google.com"))
        assert result is True
    
    def test_no_match_without_wildcard(self):
        """Non-matching permission is not granted (falls to request)."""
        pm = _make_test_pm()
        # No ui_handler → defaults to deny
        result = asyncio.run(pm.check("drive_write", "drive.google.com"))
        assert result is False


# --- Trust Level Auto-Grant Tests ---

class TestAutoGrant:
    """Test trust-level auto-granting."""
    
    def test_trust_none_does_not_auto_grant(self):
        """trust_level='none' never auto-grants."""
        pm = _make_test_pm(trust_level="none")
        result = asyncio.run(pm.check("notes_read", "local"))
        # notes_read has default=True, so it should still pass via default check
        assert result is True
        
        # But internet_access (no default) should fall to request → deny (no handler)
        result = asyncio.run(pm.check("internet_access", "tavily.com"))
        assert result is False
    
    def test_trust_low_auto_grants_low_only(self):
        """trust_level='low' auto-grants LOW-level permissions."""
        pm = _make_test_pm(trust_level="low")
        
        # read_files is LOW → should auto-grant
        result = asyncio.run(pm.check("read_files", "/some/path"))
        assert result is True
        assert any(g.permission == "read_files" and g.scope == "/some/path" for g in pm.grants)
        
        # internet_access is MEDIUM → should NOT auto-grant (no handler → deny)
        result = asyncio.run(pm.check("internet_access", "tavily.com"))
        assert result is False
    
    def test_trust_medium_auto_grants_low_and_medium(self):
        """trust_level='medium' auto-grants LOW and MEDIUM-level permissions."""
        pm = _make_test_pm(trust_level="medium")
        
        # internet_access is MEDIUM → should auto-grant
        result = asyncio.run(pm.check("internet_access", "tavily.com"))
        assert result is True
        assert any(g.permission == "internet_access" and g.scope == "tavily.com" for g in pm.grants)
        
        # run_commands is CRITICAL → should NOT auto-grant
        result = asyncio.run(pm.check("run_commands", "ls"))
        assert result is False


# --- Decorator Registration Tests ---

class TestDecoratorRegistration:
    """Test the @requires_permission decorator."""
    
    def test_decorator_registers_function(self):
        """Decorator adds the function to _DECORATOR_PERMISSIONS."""
        from atlas.security.permissions import requires_permission, _DECORATOR_PERMISSIONS
        
        @requires_permission("test_perm", "test.scope")
        def my_test_tool():
            pass
        
        assert "my_test_tool" in _DECORATOR_PERMISSIONS
        assert _DECORATOR_PERMISSIONS["my_test_tool"] == ("test_perm", "test.scope")
        
        # Clean up
        del _DECORATOR_PERMISSIONS["my_test_tool"]
    
    def test_decorator_preserves_function(self):
        """Decorator does not alter the wrapped function's behavior."""
        from atlas.security.permissions import requires_permission, _DECORATOR_PERMISSIONS
        
        @requires_permission("test_perm", "test.scope")
        def my_adder(a, b):
            return a + b
        
        assert my_adder(2, 3) == 5
        
        # Clean up
        del _DECORATOR_PERMISSIONS["my_adder"]
    
    def test_decorator_sets_attribute(self):
        """Decorator sets _atlas_permission attribute on the function."""
        from atlas.security.permissions import requires_permission, _DECORATOR_PERMISSIONS, PermissionLevel
        
        @requires_permission("email_send", "gmail.com", level=PermissionLevel.HIGH)
        def send_stuff():
            pass
        
        assert hasattr(send_stuff, "_atlas_permission")
        assert send_stuff._atlas_permission == ("email_send", "gmail.com", PermissionLevel.HIGH)
        
        # Clean up
        del _DECORATOR_PERMISSIONS["send_stuff"]


# --- Grant / Revoke / Reset Tests ---

class TestGrantManagement:
    """Test programmatic grant, revoke, and reset."""
    
    def test_grant_creates_entry(self):
        """grant() creates a PermissionGrant."""
        pm = _make_test_pm()
        asyncio.run(pm.grant("calendar_read", "calendar.google.com", duration="day"))
        
        assert any(g.permission == "calendar_read" and g.scope == "calendar.google.com" for g in pm.grants)
        grant = next(g for g in pm.grants if g.permission == "calendar_read" and g.scope == "calendar.google.com")
        assert grant.granted is True
        assert grant.granted_by == "user"
    
    def test_revoke_removes_entry(self):
        """revoke() removes the grant."""
        pm = _make_test_pm()
        asyncio.run(pm.grant("calendar_read", "*"))
        assert any(g.permission == "calendar_read" and g.scope == "*" for g in pm.grants)
        
        asyncio.run(pm.revoke("calendar_read", "*"))
        assert not any(g.permission == "calendar_read" and g.scope == "*" for g in pm.grants)
    
    def test_reset_all_clears_everything(self):
        """reset_all() clears all grants and preset."""
        pm = _make_test_pm()
        asyncio.run(pm.apply_preset("standard"))
        assert len(pm.grants) > 0
        assert pm.get_active_preset() == "standard"
        
        asyncio.run(pm.reset_all())
        assert len(pm.grants) == 0
        assert pm.get_active_preset() is None
    
    def test_grant_with_wildcard(self):
        """grant() with wildcard permission works with check()."""
        pm = _make_test_pm()
        asyncio.run(pm.grant("email_*", "gmail.com", duration="forever"))
        
        result = asyncio.run(pm.check("email_read", "gmail.com"))
        assert result is True
        
        result = asyncio.run(pm.check("email_send", "gmail.com"))
        assert result is True


# --- Permission Status Tests ---

class TestPermissionStatus:
    """Test get_permission_status dashboard data."""
    
    def test_default_status_is_unset_or_default(self):
        """Without grants, statuses show 'unset' or 'default'."""
        pm = _make_test_pm()
        statuses = pm.get_permission_status()
        
        assert len(statuses) > 0
        for s in statuses:
            assert s["status"] in ("unset", "default")
    
    def test_granted_shows_in_status(self):
        """After granting, status shows 'granted'."""
        pm = _make_test_pm()
        asyncio.run(pm.grant("email_read", "*", duration="day"))
        
        statuses = pm.get_permission_status()
        email_read = next(s for s in statuses if s["permission"] == "email_read")
        assert email_read["status"] == "granted"


# --- Request Default Duration Tests ---

class TestRequestDefaults:
    """Test suggested duration defaults."""
    
    def test_critical_suggests_once(self):
        """CRITICAL-level permissions suggest 'once' duration."""
        handler = AsyncMock(return_value={"granted": True, "duration": "once"})
        pm = _make_test_pm(ui_handler=handler)
        
        asyncio.run(pm.check("run_commands", "ls"))
        
        # Verify the request included suggested_duration
        call_args = handler.call_args[0][0]
        assert call_args["suggested_duration"] == "once"
    
    def test_medium_suggests_day(self):
        """MEDIUM-level permissions suggest 'day' duration."""
        handler = AsyncMock(return_value={"granted": True, "duration": "day"})
        pm = _make_test_pm(ui_handler=handler)
        
        asyncio.run(pm.check("internet_access", "tavily.com"))
        
        call_args = handler.call_args[0][0]
        assert call_args["suggested_duration"] == "day"

