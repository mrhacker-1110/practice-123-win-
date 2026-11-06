import base64
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class PayPalError(Exception):
    pass


def api_base_url():
    if settings.PAYPAL_MODE == "live":
        return "https://api-m.paypal.com"
    if settings.PAYPAL_MODE == "sandbox":
        return "https://api-m.sandbox.paypal.com"
    raise PayPalError("PAYPAL_MODE must be either 'sandbox' or 'live'.")


def is_configured(plan_key=None):
    required = (
        settings.PAYPAL_CLIENT_ID,
        settings.PAYPAL_CLIENT_SECRET,
        settings.PAYPAL_WEBHOOK_ID,
    )
    if not all(required):
        return False
    plan_id = {
        "pro": settings.PAYPAL_PRO_PLAN_ID,
        "super": settings.PAYPAL_SUPER_PLAN_ID,
    }.get(plan_key)
    return bool(plan_id) if plan_key else all(
        (settings.PAYPAL_PRO_PLAN_ID, settings.PAYPAL_SUPER_PLAN_ID)
    )


def _request(method, path, *, token=None, payload=None, request_id=None):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Content-Type"] = "application/json"
        if request_id:
            headers["PayPal-Request-Id"] = request_id
    else:
        credentials = f"{settings.PAYPAL_CLIENT_ID}:{settings.PAYPAL_CLIENT_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded_credentials}"
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = b"grant_type=client_credentials"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    request = Request(
        f"{api_base_url()}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read()
    except HTTPError as error:
        raise PayPalError(f"PayPal returned HTTP {error.code}.") from error
    except (TimeoutError, URLError, OSError) as error:
        raise PayPalError("Unable to contact PayPal.") from error

    try:
        return json.loads(response_body.decode("utf-8")) if response_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PayPalError("PayPal returned an invalid response.") from error


def access_token():
    if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
        raise PayPalError("PayPal API credentials are not configured.")
    result = _request("POST", "/v1/oauth2/token")
    token = result.get("access_token")
    if not token:
        raise PayPalError("PayPal did not provide an API access token.")
    return token


def api_request(method, path, payload=None, request_id=None):
    token = access_token()
    return _request(
        method,
        path,
        token=token,
        payload=payload,
        request_id=request_id,
    )


def verify_webhook(headers, event):
    if not settings.PAYPAL_WEBHOOK_ID:
        raise PayPalError("PayPal webhook ID is not configured.")
    payload = {
        "auth_algo": headers.get("PAYPAL-AUTH-ALGO", ""),
        "cert_url": headers.get("PAYPAL-CERT-URL", ""),
        "transmission_id": headers.get("PAYPAL-TRANSMISSION-ID", ""),
        "transmission_sig": headers.get("PAYPAL-TRANSMISSION-SIG", ""),
        "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME", ""),
        "webhook_id": settings.PAYPAL_WEBHOOK_ID,
        "webhook_event": event,
    }
    if not all(
        (
            payload["auth_algo"],
            payload["cert_url"],
            payload["transmission_id"],
            payload["transmission_sig"],
            payload["transmission_time"],
        )
    ):
        return False
    result = api_request(
        "POST",
        "/v1/notifications/verify-webhook-signature",
        payload,
    )
    return result.get("verification_status") == "SUCCESS"
