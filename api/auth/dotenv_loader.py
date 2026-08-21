"""
DotenvLoader — loads application secrets from a .env file.
Replaces the VaultReader module that was previously used for secret management.
"""

from pathlib import Path

from dotenv import dotenv_values


class ConfigurationException(Exception):
    """Raised when the .env file is missing or a required key is absent/empty."""

    pass


class DotenvLoader:
    """
    Loads secrets from a .env file using python-dotenv's dotenv_values().
    Secrets are stored in a private dict — they do NOT bleed into os.environ.
    """

    def __init__(self, env_path: str = None):
        """
        Load the .env file.

        Args:
            env_path: Optional explicit path to the .env file. Defaults to the project
                      root .env file (resolved relative to this file's location).

        Raises:
            ConfigurationException: If the .env file does not exist at the resolved path.
        """
        if env_path is None:
            env_path = Path(__file__).resolve().parents[2] / ".env"
        else:
            env_path = Path(env_path)

        if not env_path.exists():
            raise ConfigurationException(f"env file not found: {env_path}")

        self._secrets = dotenv_values(env_path)

    def get_secret(self, key: str) -> str:
        """
        Return the value for the given key from the loaded .env file.

        Args:
            key: The name of the secret to retrieve.

        Returns:
            The string value associated with the key.

        Raises:
            ConfigurationException: If the key is absent or its value is empty.
        """
        value = self._secrets.get(key)

        if value is None:
            raise ConfigurationException(f"Required key '{key}' is missing from .env")

        if value.strip() == "":
            raise ConfigurationException(f"Required key '{key}' is empty in .env")

        return value
