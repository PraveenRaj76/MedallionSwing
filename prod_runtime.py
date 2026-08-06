"""
Production / Streamlit Cloud runtime hardening.

Applies secrets → env only when a secrets.toml file exists (avoids Streamlit's
loud "No secrets files found" error on local runs).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parent


def secrets_toml_paths() -> List[Path]:
    return [
        _BASE / ".streamlit" / "secrets.toml",
        Path.cwd() / ".streamlit" / "secrets.toml",
        Path.home() / ".streamlit" / "secrets.toml",
    ]


def secrets_file_present() -> bool:
    return any(p.is_file() for p in secrets_toml_paths())


def is_streamlit_cloud() -> bool:
    if os.environ.get("MEDALLION_CLOUD", "").strip().lower() in {"1", "true", "yes"}:
        return True
    if os.environ.get("STREAMLIT_SHARING_MODE"):
        return True
    if os.path.exists("/mount/src"):
        return True
    if os.environ.get("STREAMLIT_RUNTIME_ENVIRONMENT", "").lower() in {
        "cloud",
        "streamlit-cloud",
    }:
        return True
    return False


def apply_streamlit_secrets() -> int:
    """
    Copy Streamlit secrets into os.environ (does not override existing env).
    Never touches st.secrets unless a secrets.toml file exists — otherwise Streamlit
    raises a visible red error on every page load.
    """
    if not secrets_file_present():
        return 0

    applied = 0
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if secrets is None:
            return 0

        def _walk(obj: Any, prefix: str = "") -> None:
            nonlocal applied
            try:
                items = dict(obj).items()
            except Exception:
                return
            for key, val in items:
                full = f"{prefix}{key}" if not prefix else f"{prefix}_{key}"
                if isinstance(val, dict) or hasattr(val, "to_dict"):
                    _walk(val, full if prefix else str(key))
                    continue
                if isinstance(val, (str, int, float, bool)):
                    env_key = str(key)
                    if env_key.startswith("MEDALLION_"):
                        if env_key not in os.environ or not str(os.environ.get(env_key, "")).strip():
                            os.environ[env_key] = str(val)
                            applied += 1
                    elif full.startswith("MEDALLION_") and (
                        full not in os.environ or not str(os.environ.get(full, "")).strip()
                    ):
                        os.environ[full] = str(val)
                        applied += 1

        _walk(secrets)
    except Exception as exc:
        logger.debug("apply_streamlit_secrets skipped: %s", exc)
    return applied


def configure_runtime() -> Dict[str, Any]:
    """Force live-friendly defaults for production hosts."""
    apply_streamlit_secrets()
    cloud = is_streamlit_cloud()
    if cloud:
        os.environ.setdefault("MEDALLION_CLOUD", "1")
        os.environ.setdefault("MEDALLION_SSL_VERIFY", "1")
        os.environ.setdefault("MEDALLION_HTTP_TIMEOUT", "15")
    os.environ.setdefault("MEDALLION_MARKET_MODE", "live")

    try:
        import nse_data_provider as nse

        nse.MARKET_MODE = os.environ.get("MEDALLION_MARKET_MODE", "live").strip().lower()
        nse.SSL_VERIFY = os.environ.get("MEDALLION_SSL_VERIFY", "0").strip() not in {
            "0",
            "false",
            "False",
            "no",
        }
        nse.HTTP_TIMEOUT = int(os.environ.get("MEDALLION_HTTP_TIMEOUT", str(nse.HTTP_TIMEOUT)))
    except Exception as exc:
        logger.debug("nse flag sync skipped: %s", exc)

    info = {
        "cloud": cloud,
        "market_mode": os.environ.get("MEDALLION_MARKET_MODE", "live"),
        "ssl_verify": os.environ.get("MEDALLION_SSL_VERIFY", "0"),
        "db_path": os.environ.get("MEDALLION_DB_PATH", ""),
        "secrets_loaded": secrets_file_present(),
    }
    logger.info("Runtime configured: %s", info)
    return info


def maybe_restore_seed_db(db_path: str) -> bool:
    """Optional cold-start restore from MEDALLION_SEED_DB_URL."""
    url = os.environ.get("MEDALLION_SEED_DB_URL", "").strip()
    if not url:
        return False
    path = Path(db_path)
    if path.exists() and path.stat().st_size > 10_000:
        return False
    try:
        from curl_cffi import requests as cr

        verify = os.environ.get("MEDALLION_SSL_VERIFY", "1").strip() not in {
            "0",
            "false",
            "False",
            "no",
        }
        resp = cr.get(url, impersonate="chrome124", timeout=60, verify=verify)
        if resp.status_code >= 400 or not resp.content:
            logger.warning("Seed DB download failed HTTP %s", resp.status_code)
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        logger.info("Restored seed DB → %s (%s bytes)", path, len(resp.content))
        return True
    except Exception as exc:
        logger.warning("Seed DB restore failed: %s", exc)
        return False
