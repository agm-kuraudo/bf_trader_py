"""
Property-based tests for the DotenvLoader, Auth, and BFDriver modules.
Uses Hypothesis to verify correctness properties across random inputs.

Validates: Requirements 2.3, 2.4, 3.3, 3.4, 5.2
"""

import os
import tempfile
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from api.auth.auth_details import Auth, AuthException
from api.auth.dotenv_loader import ConfigurationException, DotenvLoader
from BFDriver import BFDriver, BFDriverException
from logic.simpleStategy import FromFileStrategy
from output.log import Output

REQUIRED_KEYS = [
    "BF_AppKey",
    "BF_CRT_FILE",
    "BF_KEY_FILE",
    "BF_USERID",
    "BF_PWD",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PWD",
]

AUTH_KEYS = ["BF_AppKey", "BF_CRT_FILE", "BF_KEY_FILE", "BF_USERID", "BF_PWD"]

DB_KEYS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PWD"]


def _full_env_dict():
    """Return a dict with all required keys set to valid placeholder values."""
    return {
        "BF_AppKey": "test_app_key",
        "BF_CRT_FILE": "./certs/test.crt",
        "BF_KEY_FILE": "./certs/test.key",
        "BF_USERID": "test_user",
        "BF_PWD": "test_password",
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_NAME": "testdb",
        "DB_USER": "testuser",
        "DB_PWD": "testpwd",
    }


def _write_env(env_dict: dict) -> str:
    """Write a temporary .env file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False, encoding="utf-8")
    for k, v in env_dict.items():
        f.write(f"{k}={v}\n")
    f.close()
    return f.name


# Feature: replace-vault-with-dotenv, Property 1: Missing required key raises ConfigurationException
class TestProperty1:
    """
    **Validates: Requirements 2.3**

    For any required key, when absent or empty in the .env file,
    DotenvLoader.get_secret raises ConfigurationException with the key name in the message.
    """

    @given(key=st.sampled_from(REQUIRED_KEYS))
    @settings(max_examples=100)
    def test_missing_key_raises(self, key):
        """Absent key raises ConfigurationException with key name in message."""
        env_dict = _full_env_dict()
        del env_dict[key]
        tmp_path = _write_env(env_dict)

        try:
            loader = DotenvLoader(tmp_path)
            with pytest.raises(ConfigurationException, match=key):
                loader.get_secret(key)
        finally:
            os.unlink(tmp_path)

    @given(key=st.sampled_from(REQUIRED_KEYS))
    @settings(max_examples=100)
    def test_empty_key_raises(self, key):
        """Empty key raises ConfigurationException with key name in message."""
        env_dict = _full_env_dict()
        env_dict[key] = ""
        tmp_path = _write_env(env_dict)

        try:
            loader = DotenvLoader(tmp_path)
            with pytest.raises(ConfigurationException, match=key):
                loader.get_secret(key)
        finally:
            os.unlink(tmp_path)


# Feature: replace-vault-with-dotenv, Property 2: Present key returns correct string value
class TestProperty2:
    """
    **Validates: Requirements 2.4**

    For any key-value pair written to .env, get_secret returns the exact string value.
    """

    @given(
        key=st.from_regex(r"[A-Z][A-Z0-9_]{0,20}", fullmatch=True),
        value=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P", "S"), blacklist_characters="\n\r\x00#='\""),
            min_size=1,
            max_size=50,
        ).filter(lambda v: v.isascii()),
    )
    @settings(max_examples=100)
    def test_present_key_returns_value(self, key, value):
        """Present key returns the exact string value written."""
        tmp_path = _write_env({key: value})

        try:
            loader = DotenvLoader(tmp_path)
            assert loader.get_secret(key) == value
        finally:
            os.unlink(tmp_path)


# Feature: replace-vault-with-dotenv, Property 3: Auth credential key absence raises AuthException
class TestProperty3:
    """
    **Validates: Requirements 3.3, 3.4**

    For any auth key, when absent, Auth raises AuthException with key name in message.
    """

    @given(key=st.sampled_from(AUTH_KEYS))
    @settings(max_examples=100)
    def test_missing_auth_key_raises(self, key):
        """Missing auth key raises AuthException identifying the missing key."""
        env_dict = _full_env_dict()
        del env_dict[key]
        tmp_path = _write_env(env_dict)

        try:
            loader = DotenvLoader(tmp_path)
            if key in ["BF_AppKey", "BF_CRT_FILE", "BF_KEY_FILE"]:
                # These are read at Auth.__init__ time
                with pytest.raises(AuthException, match=key):
                    Auth(loader)
            else:
                # BF_USERID, BF_PWD are read when get_credentials is called
                auth = Auth(loader)
                with pytest.raises(AuthException, match=key):
                    auth.get_credentials()
        finally:
            os.unlink(tmp_path)


# Feature: replace-vault-with-dotenv, Property 4: Missing DB key raises BFDriverException
class TestProperty4:
    """
    **Validates: Requirements 5.2**

    For any DB key, when absent, BFDriver.get_local_db_details raises BFDriverException
    with the key name in the message.

    Note: BFDriver.__init__ creates its own DotenvLoader internally, so we construct
    BFDriver with a complete .env (needed for Auth init), then replace the internal
    loader with one missing the target DB key before calling get_local_db_details.
    """

    @given(key=st.sampled_from(DB_KEYS))
    @settings(max_examples=100)
    def test_missing_db_key_raises(self, key):
        """Missing DB key raises BFDriverException identifying the missing key."""
        # Create a full .env for BFDriver construction
        full_dict = _full_env_dict()
        full_path = _write_env(full_dict)

        # Create a .env with the target DB key removed for the patched loader
        partial_dict = _full_env_dict()
        del partial_dict[key]
        partial_path = _write_env(partial_dict)

        try:
            Output.LOG_FILE = False
            Output.LOG_CONSOLE = False

            # Construct BFDriver with the full .env by patching DotenvLoader
            with patch("BFDriver.DotenvLoader") as MockLoaderClass:
                full_loader = DotenvLoader(full_path)
                MockLoaderClass.return_value = full_loader
                bf = BFDriver(FromFileStrategy(), Output.ERROR)

            # Now replace the internal loader with one missing the DB key
            partial_loader = DotenvLoader(partial_path)
            bf._BFDriver__loader = partial_loader

            with pytest.raises(BFDriverException, match=key):
                bf.get_local_db_details()
        finally:
            os.unlink(full_path)
            os.unlink(partial_path)
