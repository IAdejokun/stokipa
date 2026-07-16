"""Meta webhook signature verification (X-Hub-Signature-256).

CRITICAL: the HMAC must be computed over the RAW request bytes exactly as
received. Never re-serialize the parsed JSON — key order / whitespace changes
break the digest.
"""

import hashlib
import hmac


def verify_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])