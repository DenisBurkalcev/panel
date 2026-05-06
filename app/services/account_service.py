"""High-level helpers tying the FunPay client to the database layer."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crypto import decrypt_str
from app.models.account import Account
from app.security import utcnow
from app.services.funpay_client import (
    FunPayClient,
    FunPayCredentials,
    FunPayError,
)

# Truncate FunPay error messages we persist to the DB so a verbose stack-trace
# (which can happen on httpx connection errors) doesn't blow past the column
# limit. The model declares `last_check_error` as `String(500)`.
_ERROR_TRUNC_LEN = 480


def credentials_for(account: Account) -> FunPayCredentials:
    proxy = decrypt_str(account.proxy_url_enc) if account.proxy_url_enc else None
    return FunPayCredentials(
        golden_key=decrypt_str(account.golden_key_enc),
        user_agent=account.user_agent,
        proxy_url=proxy,
    )


async def probe_account(account: Account, db: Session) -> tuple[bool, str | None]:
    """Hit the FunPay home page and update cached profile info on the account."""
    try:
        async with FunPayClient(credentials_for(account)) as client:
            profile = await client.fetch_profile()
    except FunPayError as exc:
        # FunPayAuthError is a subclass of FunPayError so this catches both.
        account.last_check_ok = False
        account.last_check_error = str(exc)[:_ERROR_TRUNC_LEN]
        account.last_checked_at = utcnow()
        db.add(account)
        db.commit()
        return False, str(exc)
    account.last_check_ok = True
    account.last_check_error = None
    account.last_checked_at = utcnow()
    account.funpay_user_id = profile.user_id
    account.funpay_username = profile.username
    db.add(account)
    db.commit()
    return True, None
