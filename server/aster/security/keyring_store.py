# 密钥存取：唯一真源在 macOS Keychain，进程内短持有，不落库不进日志
import keyring

from ..config import KEYCHAIN_SERVICE


def get_secret(name: str) -> str | None:
    try:
        return keyring.get_password(KEYCHAIN_SERVICE, name)
    except keyring.errors.KeyringError:
        return None


def set_secret(name: str, value: str) -> None:
    keyring.set_password(KEYCHAIN_SERVICE, name, value)


def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(KEYCHAIN_SERVICE, name)
    except keyring.errors.KeyringError:
        pass
