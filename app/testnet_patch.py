#!/usr/bin/env python3
"""
TIMPAL Testnet Storage Patch
Dynamically intercepts 'storage' imports and redirects to 'storage_fallback'
for cross-platform compatibility (LMDB on macOS, LevelDB on Linux).

CRITICAL: This patch is ONLY for testnet. Mainnet code is never modified.
"""

import sys
import importlib.util


class StorageImportInterceptor:
    """
    Import hook that redirects 'storage' module to 'storage_fallback'
    Only active when explicitly enabled by testnet runners.
    """
    
    def __init__(self):
        # __builtins__ can be either a dict or a module depending on context
        if isinstance(__builtins__, dict):
            self.original_import = __builtins__['__import__']
        else:
            self.original_import = __builtins__.__import__
        self.enabled = False
    
    def enable(self):
        """Enable the import interception"""
        if not self.enabled:
            # Handle both dict and module forms of __builtins__
            if isinstance(__builtins__, dict):
                __builtins__['__import__'] = self._custom_import
            else:
                __builtins__.__import__ = self._custom_import
            self.enabled = True
            print("🔧 Testnet Patch: Storage import interception enabled")
            print("   → macOS: Will use LMDB (zero compilation)")
            print("   → Linux: Will use LevelDB (production)")
    
    def disable(self):
        """Disable the import interception"""
        if self.enabled:
            # Handle both dict and module forms of __builtins__
            if isinstance(__builtins__, dict):
                __builtins__['__import__'] = self.original_import
            else:
                __builtins__.__import__ = self.original_import
            self.enabled = False
    
    def _custom_import(self, name, *args, **kwargs):
        """
        Custom import function that intercepts 'storage' and redirects to 'storage_fallback'
        All other imports pass through unchanged.
        """
        # Intercept direct 'storage' imports in testnet context
        if name == 'storage' or name == 'app.storage':
            # Redirect to storage_fallback
            try:
                if name == 'storage':
                    return self.original_import('storage_fallback', *args, **kwargs)
                else:  # app.storage
                    return self.original_import('app.storage_fallback', *args, **kwargs)
            except ImportError:
                # Fallback to original if storage_fallback doesn't exist
                return self.original_import(name, *args, **kwargs)
        
        # All other imports pass through unchanged
        return self.original_import(name, *args, **kwargs)


# Global interceptor instance
_interceptor = StorageImportInterceptor()


def apply_testnet_patch():
    """
    Apply the testnet cross-platform storage patch.
    Call this BEFORE importing any blockchain modules in testnet runners.
    
    This function:
    1. Enables storage import interception
    2. Redirects 'storage' → 'storage_fallback'
    3. Ensures cross-platform compatibility (LMDB/LevelDB)
    4. Does NOT affect mainnet code
    """
    _interceptor.enable()


def remove_testnet_patch():
    """
    Remove the testnet patch (usually not needed, but available for cleanup)
    """
    _interceptor.disable()


if __name__ == "__main__":
    print("="*70)
    print("  TIMPAL Testnet Storage Patch")
    print("="*70)
    print("\nThis module provides cross-platform storage for testnet:")
    print("  • macOS → LMDB (zero compilation issues)")
    print("  • Linux → LevelDB (production performance)")
    print("\nUsage in testnet runners:")
    print("  from app.testnet_patch import apply_testnet_patch")
    print("  apply_testnet_patch()  # BEFORE importing blockchain modules")
    print("\nMainnet code remains 100% unchanged.")
    print("="*70)
