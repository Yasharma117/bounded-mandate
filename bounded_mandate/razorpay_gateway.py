"""Settlement — the only component that holds Razorpay credentials.

The agent never reaches this module, and neither does anything the agent can
influence. The engine calls it after a proposal has already been authorised.

Two legs, and they are not the same shape:

- **Registration** is user-present. A ₹1 UPI Autopay authorisation runs through
  Razorpay's Standard Checkout, the user approves the mandate in their PSP app
  (RBI's one-time AFA), and Razorpay hands back a token. `create_mandate_order`
  and `verify_registration` are that leg.
- **Charging** is user-absent, which is the entire point of the product. The
  engine debits the token server-side with nobody watching. `charge` is that
  leg, and it has no checkout, no modal and no confirmation step.

Confusing the two would collapse the thesis into a confirm dialog.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

# The ₹1 authorisation transaction that registers a UPI Autopay mandate.
AUTH_AMOUNT_PAISE = 100

# Razorpay rejects anything smaller.
MIN_AMOUNT_PAISE = 100

# Measured against the live test API: this merchant category tops out at
# ₹1,00,000 per debit. Anything above is rejected at order creation.
MAX_MANDATE_AMOUNT_PAISE = 10_000_000

DEFAULT_MANDATE_YEARS = 5


class GatewayError(Exception):
    """Razorpay refused, or could not be reached."""


class GatewayAuthError(GatewayError):
    """Razorpay rejected our credentials — a deployment fault, not a user fault."""


class SignatureMismatch(GatewayError):
    """A payment callback did not carry a signature we can verify. Never trust it."""


def _wrap(exc: Exception, what: str) -> GatewayError:
    """Classify an SDK error. Razorpay signals bad keys as a BadRequestError
    reading 'Authentication failed', so the string is all there is to go on."""
    kind = GatewayAuthError if "authentication failed" in str(exc).lower() else GatewayError
    return kind(f"{what}: {exc}")


@dataclass(frozen=True)
class MandateOrder:
    """Everything the registration page needs — and nothing it must not have."""

    order_id: str
    customer_id: str
    amount_paise: int
    key_id: str  # public by design; the secret never leaves this process


class RazorpayGateway:
    def __init__(
        self,
        key_id: str | None = None,
        key_secret: str | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self.key_id = key_id or os.environ.get("RAZORPAY_KEY_ID", "")
        self._key_secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET", "")
        if client is None:
            if not (self.key_id and self._key_secret):
                raise GatewayError(
                    "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set "
                    "(they live in .env, which is gitignored)."
                )
            import razorpay

            client = razorpay.Client(auth=(self.key_id, self._key_secret))
        self.client = client

    # --- registration: user-present, once ------------------------------------

    def create_customer(self, name: str, email: str, contact: str) -> str:
        try:
            created = self.client.customer.create(
                {"name": name, "email": email, "contact": contact, "fail_existing": "0"}
            )
        except Exception as exc:
            raise _wrap(exc, "could not create customer") from exc
        return created["id"]

    def create_mandate_order(
        self,
        customer_id: str,
        *,
        max_amount_paise: int,
        frequency: str = "as_presented",
        expire_at: int | None = None,
    ) -> MandateOrder:
        """The ₹1 authorisation order that Standard Checkout registers against.

        `as_presented` is the whole reason this is UPI Autopay and not a
        subscription: the debit amount varies per order, so the mandate carries
        a ceiling rather than a schedule.
        """
        if not MIN_AMOUNT_PAISE <= max_amount_paise <= MAX_MANDATE_AMOUNT_PAISE:
            raise GatewayError(
                f"mandate cap must be between {MIN_AMOUNT_PAISE} and "
                f"{MAX_MANDATE_AMOUNT_PAISE} paise, got {max_amount_paise}"
            )
        expire_at = expire_at or int(time.time()) + DEFAULT_MANDATE_YEARS * 365 * 86_400
        try:
            order = self.client.order.create(
                {
                    "amount": AUTH_AMOUNT_PAISE,
                    "currency": "INR",
                    "receipt": f"mandate_{customer_id}",
                    "method": "upi",
                    "customer_id": customer_id,
                    "token": {
                        "max_amount": max_amount_paise,
                        "expire_at": expire_at,
                        "frequency": frequency,
                    },
                }
            )
        except Exception as exc:
            raise _wrap(exc, "could not create mandate order") from exc
        return MandateOrder(order["id"], customer_id, AUTH_AMOUNT_PAISE, self.key_id)

    def verify_registration(self, order_id: str, payment_id: str, signature: str) -> None:
        """Confirm the callback really came from Razorpay. Raises if it did not.

        The SDK compares with `hmac.compare_digest`, so this is constant-time.
        A mismatch means the browser is lying about a payment; nothing is
        registered and nothing is charged.
        """
        try:
            self.client.utility.verify_payment_signature(
                {
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": signature,
                }
            )
        except Exception as exc:
            raise SignatureMismatch(f"payment signature did not verify: {exc}") from exc

    def token_for(self, payment_id: str) -> str | None:
        """The mandate token minted by a verified authorisation payment."""
        try:
            return self.client.payment.fetch(payment_id).get("token_id")
        except Exception as exc:
            raise _wrap(exc, f"could not read payment {payment_id}") from exc

    # --- charging: user-absent, every time -----------------------------------

    def create_charge_order(
        self, *, amount_paise: int, idempotency_key: str, description: str
    ) -> str:
        """Put an authorised charge on Razorpay's rails, server-side.

        This half needs no mandate and no human — the engine calls it the moment
        a proposal is allowed. On an account with recurring enabled, `charge`
        goes on to debit the mandate token. Without one, the order is as far as
        the money leg can go, and the ledger records exactly that rather than
        implying a completed debit.
        """
        if amount_paise < MIN_AMOUNT_PAISE:
            raise GatewayError(f"amount must be at least {MIN_AMOUNT_PAISE} paise")
        try:
            order = self.client.order.create(
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": idempotency_key[:40],
                    "notes": {"description": description},
                }
            )
        except Exception as exc:
            raise _wrap(exc, "could not create charge order") from exc
        return order["id"]

    def charge(
        self,
        *,
        token_id: str,
        customer_id: str,
        email: str,
        contact: str,
        amount_paise: int,
        description: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Debit an existing mandate. No checkout, no modal, nobody watching.

        `idempotency_key` rides along as the receipt so a retried charge is
        identifiable at Razorpay's end as well as in our ledger.
        """
        order_id = self.create_charge_order(
            amount_paise=amount_paise, idempotency_key=idempotency_key, description=description
        )
        try:
            return self.client.payment.createRecurring(
                {
                    "email": email,
                    "contact": contact,
                    "amount": amount_paise,
                    "currency": "INR",
                    "order_id": order_id,
                    "customer_id": customer_id,
                    "token": token_id,
                    "recurring": "1",
                    "description": description,
                }
            )
        except Exception as exc:
            raise _wrap(exc, "charge failed") from exc

    # --- webhooks ------------------------------------------------------------

    def verify_webhook(self, body: bytes, signature: str, secret: str) -> None:
        """Raises unless the payload really came from Razorpay."""
        try:
            self.client.utility.verify_webhook_signature(body.decode(), signature, secret)
        except Exception as exc:
            raise SignatureMismatch(f"webhook signature did not verify: {exc}") from exc
