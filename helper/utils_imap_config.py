from __future__ import annotations

from dataclasses import dataclass
import os

from helper.helper_config import get_env_flag, get_env_int, get_env_str  # type: ignore


@dataclass
class ImapConfig:
    host: str
    port: int
    user: str
    password: str
    folder: str
    verify_ssl: bool
    timeout: int


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    use_ssl: bool
    use_starttls: bool
    default_from: str


def load_imap_config(
    folder_env: str,
    default_folder: str,
    *,
    require_credentials: bool = False,
) -> ImapConfig | None:
    """
    Load IMAP configuration from environment.

    When require_credentials is True, missing IMAP_HOST/USER/PASSWORD raises
    RuntimeError (used by the fetcher); otherwise returns None so callers can
    gracefully skip IMAP operations (used by the sender).
    """
    host = get_env_str("IMAP_HOST", "")
    user = get_env_str("ALI_USERNAME", "")
    password = get_env_str("ALI_PASSWORD", "")

    if not host or not user or not password:
        if require_credentials:
            raise RuntimeError("IMAP_HOST / ALI_USERNAME / ALI_PASSWORD 必須設定。")
        return None

    port = get_env_int("IMAP_PORT", 993)
    folder = get_env_str(folder_env, default_folder)
    verify_ssl = get_env_flag("IMAP_VERIFY_SSL", True)
    timeout = get_env_int("IMAP_TIMEOUT", 300)

    return ImapConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        folder=folder,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )


def load_smtp_config() -> SmtpConfig | None:
    """
    Load SMTP configuration from environment.

    Returns None when required SMTP_HOST/USER/PASSWORD are missing so callers
    can fail gracefully.
    """
    host = get_env_str("SMTP_HOST", "")
    user = get_env_str("ALI_USERNAME", "")
    password = get_env_str("ALI_PASSWORD", "")
    if not host or not user or not password:
        return None

    port = get_env_int("SMTP_PORT", 587)
    default_from = os.getenv("ALI_ASSISTANT_EMAIL", user)
    use_ssl = get_env_flag("SMTP_USE_SSL", False)
    use_starttls = get_env_flag("SMTP_STARTTLS", True)

    return SmtpConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        use_ssl=use_ssl,
        use_starttls=use_starttls,
        default_from=default_from,
    )
