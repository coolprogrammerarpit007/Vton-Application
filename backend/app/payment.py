import hashlib
import json
import time
import random
import re
import logging
from calendar import monthrange
from datetime import datetime, timedelta, date

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import BigInteger, Column, Date, DateTime, Integer, Numeric, String, Text, JSON as SAJSON, func

from . import models, schemas
from .database import get_db
from .config import settings
from .exceptions import APIException
from .auth import get_current_user
from .fashn_service import check_fashn_master_balance


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

router = APIRouter(prefix="/api/payment", tags=["Payment Integration"])

PAYU_CURRENCY = getattr(settings, "PAYU_CURRENCY", "INR")


def _payu_str(value) -> str:
    if value is None:
        return ""
    return str(value)


def generate_request_hash(data: dict, si_details: str = "") -> str:
    """
    PayU hosted checkout hash — same formula as PHP PayuService::generatePaymentHash:
    SHA512(key|txnid|amount|productinfo|firstname|email|udf1|udf2|udf3|udf4|udf5||||||SALT)

    Autopay still posts si=1 and si_details, but those fields are NOT part of this hash.
    """
    _ = si_details
    hash_sequence = (
        f"{_payu_str(settings.PAYU_MERCHANT_KEY)}|{_payu_str(data.get('txnid'))}|"
        f"{_payu_str(data.get('amount'))}|{_payu_str(data.get('productinfo'))}|"
        f"{_payu_str(data.get('firstname'))}|{_payu_str(data.get('email'))}|"
        f"{_payu_str(data.get('udf1'))}|{_payu_str(data.get('udf2'))}|"
        f"{_payu_str(data.get('udf3'))}|{_payu_str(data.get('udf4'))}|"
        f"{_payu_str(data.get('udf5'))}||||||{_payu_str(settings.PAYU_MERCHANT_SALT)}"
    )
    return hashlib.sha512(hash_sequence.encode("utf-8")).hexdigest().lower()


def _sha512_pipe(sequence: str) -> str:
    return hashlib.sha512(sequence.encode('utf-8')).hexdigest().lower()


def validate_response_hash(data: dict) -> bool:
    received_hash = (data.get('hash') or '').lower()
    if not received_hash:
        return False

    status = data.get('status', '')
    si_details = data.get('si_details') or ''
    additional_charges = data.get('additionalCharges') or data.get('additional_charges') or ''

    reverse_tail = (
        f"{data.get('udf5', '')}|{data.get('udf4', '')}|{data.get('udf3', '')}|"
        f"{data.get('udf2', '')}|{data.get('udf1', '')}|{data.get('email', '')}|"
        f"{data.get('firstname', '')}|{data.get('productinfo', '')}|"
        f"{data.get('amount', '')}|{data.get('txnid', '')}|{settings.PAYU_MERCHANT_KEY}"
    )

    candidates = [
        f"{settings.PAYU_MERCHANT_SALT}|{status}||||||{reverse_tail}",
    ]
    if si_details:
        candidates.append(
            f"{settings.PAYU_MERCHANT_SALT}|{status}|{si_details}||||||{reverse_tail}"
        )

    if additional_charges:
        candidates.extend([f"{additional_charges}|{seq}" for seq in list(candidates)])

    return any(_sha512_pipe(seq) == received_hash for seq in candidates)


def extract_enum_or_val(obj, attr_name: str, default=None):
    val = getattr(obj, attr_name, default)
    if hasattr(val, "value"):
        return val.value
    return val


def format_payu_amount(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value or "0.00")


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, month=2, day=28)


def resolve_payu_billing(plan) -> tuple[str, int]:
    """
    Map plan billing to PayU SI values.
    PayU accepts ONCE, ADHOC, DAILY, WEEKLY, MONTHLY, YEARLY.
    Quarterly / half-yearly become MONTHLY with interval 3 / 6.
    """
    raw_cycle = str(extract_enum_or_val(plan, "billing_cycle", "MONTHLY") or "MONTHLY")
    raw_cycle = raw_cycle.upper().replace("-", "_").replace(" ", "_")
    interval = max(1, int(getattr(plan, "billing_interval", 1) or 1))

    mapping = {
        "MONTHLY": ("MONTHLY", interval),
        "QUARTERLY": ("MONTHLY", 3 * interval),
        "HALF_YEARLY": ("MONTHLY", 6 * interval),
        "HALFYEARLY": ("MONTHLY", 6 * interval),
        "YEARLY": ("YEARLY", interval),
        "YEAR": ("YEARLY", interval),
        "WEEKLY": ("WEEKLY", interval),
        "DAILY": ("DAILY", interval),
    }
    return mapping.get(raw_cycle, ("MONTHLY", interval))


def add_billing_period(dt: datetime, billing_cycle: str, billing_interval: int) -> datetime:
    cycle = (billing_cycle or "MONTHLY").upper()
    interval = max(1, int(billing_interval or 1))
    if cycle == "YEARLY":
        return _add_months(dt, 12 * interval)
    if cycle == "WEEKLY":
        return dt + timedelta(weeks=interval)
    if cycle == "DAILY":
        return dt + timedelta(days=interval)
    return _add_months(dt, interval)


def build_si_details(amount: str, billing_cycle: str, billing_interval: int) -> dict:
    start = datetime.now().date()
    return {
        "billingAmount": amount,
        "billingCurrency": PAYU_CURRENCY,
        "billingCycle": billing_cycle,
        "billingInterval": billing_interval,
        "paymentStartDate": start.isoformat(),
        "paymentEndDate": _add_years(start, 30).isoformat(),
    }


def encode_si_details(si_details: dict) -> str:
    # Match PHP json_encode(..., JSON_UNESCAPED_SLASHES): compact, no extra spaces.
    return json.dumps(si_details, separators=(",", ":"), ensure_ascii=True)


def _resolve_sqlalchemy_base():
    for candidate in (getattr(models, "Base", None), getattr(models, "BaseModel", None)):
        if candidate is not None:
            return candidate
    try:
        from .database import Base as DatabaseBase
        return DatabaseBase
    except Exception:
        from sqlalchemy.orm import declarative_base
        return declarative_base()


if hasattr(models, "PayuSubscription") and hasattr(models, "PayuRecurringPaymentLog"):
    PayuSubscription = models.PayuSubscription
    PayuRecurringPaymentLog = models.PayuRecurringPaymentLog
else:
    _PayuBase = _resolve_sqlalchemy_base()

    class PayuSubscription(_PayuBase):
        __tablename__ = "payu_subscriptions"
        __table_args__ = {"extend_existing": True}

        id = Column(BigInteger, primary_key=True, autoincrement=True)
        user_id = Column(BigInteger, nullable=False, index=True)
        plan_id = Column(BigInteger, nullable=False, index=True)
        user_subscription_id = Column(BigInteger, nullable=True, index=True)
        subscription_reference = Column(String(64), nullable=False, unique=True)
        payu_mandate_id = Column(String(64), nullable=True, index=True)
        payu_txnid = Column(String(64), nullable=True, index=True)
        amount = Column(Numeric(12, 2), nullable=False)
        billing_cycle = Column(String(32), nullable=False)
        billing_interval = Column(Integer, nullable=False, default=1)
        start_date = Column(Date, nullable=True)
        next_billing_date = Column(Date, nullable=True, index=True)
        end_date = Column(Date, nullable=True)
        last_charge_date = Column(DateTime, nullable=True)
        status = Column(String(32), nullable=False, default="pending", index=True)
        payment_mode = Column(String(32), nullable=True)
        retry_count = Column(Integer, nullable=False, default=0)
        next_retry_at = Column(DateTime, nullable=True)
        pre_debit_notified_at = Column(DateTime, nullable=True)
        pre_debit_request_id = Column(String(64), nullable=True)
        mandate_seq_no = Column(Integer, nullable=False, default=1)
        remarks = Column(Text, nullable=True)
        mandate_response = Column(SAJSON, nullable=True)
        revoke_response = Column(SAJSON, nullable=True)
        created_at = Column(DateTime, default=datetime.utcnow)
        updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    class PayuRecurringPaymentLog(_PayuBase):
        __tablename__ = "payu_recurring_payment_logs"
        __table_args__ = {"extend_existing": True}

        id = Column(BigInteger, primary_key=True, autoincrement=True)
        subscription_id = Column(BigInteger, nullable=True, index=True)
        txnid = Column(String(64), nullable=False, unique=True)
        payu_payment_id = Column(String(64), nullable=True, index=True)
        amount = Column(Numeric(12, 2), nullable=False)
        status = Column(String(32), nullable=False, default="pending", index=True)
        attempt_number = Column(Integer, nullable=False, default=1)
        type = Column(String(32), nullable=False, default="charge", index=True)
        request_payload = Column(SAJSON, nullable=True)
        response_payload = Column(SAJSON, nullable=True)
        processed_at = Column(DateTime, nullable=True)
        created_at = Column(DateTime, default=datetime.utcnow)
        updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def plan_billing_cycle_value(plan) -> str:
    raw_cycle = str(extract_enum_or_val(plan, "billing_cycle", "MONTHLY") or "MONTHLY")
    raw_cycle = raw_cycle.upper().replace("-", "_").replace(" ", "_")
    allowed = {"MONTHLY", "QUARTERLY", "HALF_YEARLY", "YEARLY"}
    return raw_cycle if raw_cycle in allowed else "MONTHLY"


def generate_subscription_reference() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "SUB-" + "".join(random.choice(alphabet) for _ in range(12))


def jsonable_payload(data: dict) -> dict:
    clean = {}
    for key, value in (data or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[str(key)] = value
        else:
            clean[str(key)] = str(value)
    return clean


def to_decimal_amount(value) -> Decimal:
    try:
        return Decimal(format_payu_amount(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def find_payu_subscription(db: Session, txnid: str = "", mandate_id: str = "", reference: str = ""):
    query = db.query(PayuSubscription)
    if txnid:
        found = query.filter(PayuSubscription.payu_txnid == txnid).first()
        if found:
            return found
    if mandate_id:
        found = query.filter(PayuSubscription.payu_mandate_id == mandate_id).first()
        if found:
            return found
    if reference:
        found = query.filter(PayuSubscription.subscription_reference == reference).first()
        if found:
            return found
    return None


def create_pending_payu_subscription(
    db: Session,
    *,
    user_id: int,
    plan,
    txnid: str,
    amount: str,
    si_details: dict,
    payu_billing_cycle: str,
    payu_billing_interval: int,
):
    now = datetime.utcnow()
    next_billing = add_billing_period(now, payu_billing_cycle, payu_billing_interval).date()
    subscription = PayuSubscription(
        user_id=user_id,
        plan_id=plan.id,
        user_subscription_id=None,
        subscription_reference=generate_subscription_reference(),
        payu_mandate_id=None,
        payu_txnid=txnid,
        amount=to_decimal_amount(amount),
        billing_cycle=plan_billing_cycle_value(plan),
        billing_interval=max(1, int(getattr(plan, "billing_interval", 1) or 1)),
        start_date=now.date(),
        next_billing_date=next_billing,
        end_date=_add_years(now.date(), 30),
        last_charge_date=None,
        status="pending",
        payment_mode=None,
        retry_count=0,
        next_retry_at=None,
        mandate_seq_no=1,
        remarks="Mandate initiated",
        mandate_response={"si_details": si_details, "type": "mandate_consent"},
        created_at=now,
        updated_at=now,
    )
    db.add(subscription)
    return subscription


def upsert_payu_consent_log(
    db: Session,
    *,
    subscription,
    txnid: str,
    payu_payment_id: str,
    amount,
    status: str,
    payload: dict,
    now: datetime,
):
    log = db.query(PayuRecurringPaymentLog).filter(PayuRecurringPaymentLog.txnid == txnid).first()
    request_payload = {
        "type": "mandate_consent",
        "subscription_reference": getattr(subscription, "subscription_reference", None),
    }
    response_payload = jsonable_payload(payload)

    if log:
        log.subscription_id = getattr(subscription, "id", None)
        log.payu_payment_id = payu_payment_id or log.payu_payment_id
        log.amount = to_decimal_amount(amount)
        log.status = status
        log.attempt_number = getattr(log, "attempt_number", 1) or 1
        log.type = "consent"
        log.request_payload = request_payload
        log.response_payload = response_payload
        log.processed_at = now
        log.updated_at = now
        return log

    log = PayuRecurringPaymentLog(
        subscription_id=getattr(subscription, "id", None),
        txnid=txnid,
        payu_payment_id=payu_payment_id or None,
        amount=to_decimal_amount(amount),
        status=status,
        attempt_number=1,
        type="consent",
        request_payload=request_payload,
        response_payload=response_payload,
        processed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(log)
    return log


def save_payu_subscription_on_success(
    db: Session,
    *,
    user_id: int,
    plan,
    user_subscription_id: int,
    txnid: str,
    amount,
    mandate_id: str,
    payu_billing_cycle: str,
    payu_billing_interval: int,
    cycle_end: datetime,
    payload: dict,
    now: datetime,
):
    payment_mode = str(payload.get("mode") or payload.get("bankcode") or "")[:32] or None
    start_date = now.date()
    if isinstance(cycle_end, datetime):
        next_billing = cycle_end.date()
    else:
        next_billing = add_billing_period(now, payu_billing_cycle, payu_billing_interval).date()
    end_date = _add_years(start_date, 30)

    subscription = find_payu_subscription(db, txnid=txnid, mandate_id=mandate_id)
    if subscription is None:
        subscription = PayuSubscription(
            user_id=user_id,
            plan_id=plan.id,
            subscription_reference=generate_subscription_reference(),
            payu_txnid=txnid,
            amount=to_decimal_amount(amount),
            billing_cycle=plan_billing_cycle_value(plan),
            billing_interval=max(1, int(getattr(plan, "billing_interval", 1) or 1)),
            status="pending",
            retry_count=0,
            mandate_seq_no=1,
            created_at=now,
        )
        db.add(subscription)
        db.flush()

    subscription.user_id = user_id
    subscription.plan_id = plan.id
    subscription.user_subscription_id = user_subscription_id
    subscription.payu_mandate_id = mandate_id or getattr(subscription, "payu_mandate_id", None)
    subscription.payu_txnid = txnid or getattr(subscription, "payu_txnid", None)
    subscription.amount = to_decimal_amount(amount)
    subscription.billing_cycle = plan_billing_cycle_value(plan)
    subscription.billing_interval = max(1, int(getattr(plan, "billing_interval", 1) or 1))
    subscription.start_date = start_date
    subscription.next_billing_date = next_billing
    subscription.end_date = end_date
    subscription.last_charge_date = now
    subscription.status = "active"
    subscription.payment_mode = payment_mode
    subscription.retry_count = 0
    subscription.next_retry_at = None
    subscription.mandate_seq_no = 1
    subscription.remarks = "Mandate activated"
    subscription.mandate_response = jsonable_payload(payload)
    subscription.updated_at = now

    db.flush()
    upsert_payu_consent_log(
        db,
        subscription=subscription,
        txnid=txnid,
        payu_payment_id=mandate_id,
        amount=amount,
        status="success",
        payload=payload,
        now=now,
    )
    logger.info(
        f"Saved PayU Autopay records | sub_ref={subscription.subscription_reference} | "
        f"mandate={mandate_id} | txnid={txnid}"
    )
    return subscription


def save_payu_subscription_on_failure(
    db: Session,
    *,
    txnid: str,
    mandate_id: str,
    payload: dict,
    now: datetime,
    amount=None,
):
    subscription = find_payu_subscription(db, txnid=txnid, mandate_id=mandate_id)
    if subscription is None:
        return None

    if getattr(subscription, "status", None) != "active":
        subscription.status = "failed"
        subscription.mandate_response = jsonable_payload(payload)
        subscription.remarks = "Mandate consent failed: " + str(
            payload.get("error_Message") or payload.get("error") or payload.get("status") or "failed"
        )
        subscription.updated_at = now

    upsert_payu_consent_log(
        db,
        subscription=subscription,
        txnid=txnid or getattr(subscription, "payu_txnid", ""),
        payu_payment_id=mandate_id,
        amount=amount if amount is not None else getattr(subscription, "amount", 0),
        status="failed",
        payload=payload,
        now=now,
    )
    return subscription


@router.post("/initiate", response_model=schemas.StandardPaymentResponse)
async def initiate_payment(
    req: schemas.PaymentInitiateRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    clean_req_plan = req.plan_name.strip().lower()
    
    plan = db.query(models.SubscriptionPlan).filter(
        (func.lower(func.trim(models.SubscriptionPlan.plan_name)) == clean_req_plan) |
        (func.lower(func.trim(models.SubscriptionPlan.title)).like(f"%{clean_req_plan}%")),
        models.SubscriptionPlan.is_active == True
    ).first()

    if not plan:
        logger.error(f"Initiate failed: No active plan found matching '{req.plan_name}'")
        raise APIException(status_code=200, msg=f"Invalid or inactive subscription plan '{req.plan_name}' selected.")
    
    # --- PRE-FLIGHT CHECK: Calculate Internal Virtual Reserve from mpx_fashn_api_payments ---
    plan_required_virtual_credits = plan.credits or 0
    
    # Fetch the exact running virtual reserve from the latest ledger entry
    latest_ledger_entry = (
        db.query(models.MpxFashnApiPayment.virtual_balance_after)
        .order_by(models.MpxFashnApiPayment.id.desc())
        .first()
    )
    
    virtual_reserve = float(latest_ledger_entry[0]) if latest_ledger_entry and latest_ledger_entry[0] is not None else 0.0
    
    # Block the sale ONLY if your virtual credit box cannot support the plan
    if virtual_reserve < plan_required_virtual_credits:
        logger.critical(
            f"[PAYMENT INITIATE BLOCKED] Master Virtual Reserve ({virtual_reserve}) < plan credits ({plan_required_virtual_credits})."
        )
        raise APIException(
            status_code=200,
            msg="Subscription purchases are temporarily paused due to maximum capacity. Please try again shortly."
        )
    
    user_firstname = (current_user.full_name or current_user.username or "Customer")
    user_email = current_user.email or ""
    amount_str = format_payu_amount(plan.total_price)
    billing_cycle, billing_interval = resolve_payu_billing(plan)
    si_details = build_si_details(amount_str, billing_cycle, billing_interval)
    si_details_json = encode_si_details(si_details)

    txnid = f"VTONSI{int(time.time())}{random.randint(1000, 9999)}"
    
    transaction = models.PaymentTransaction(
        txnid=txnid,
        user_id=current_user.id,
        amount=amount_str,           
        product_info=plan.title,     
        firstname=user_firstname,
        email=user_email,
        phone=req.phone,             
        status=models.TransactionStatus.PENDING
    )
    db.add(transaction)
    db.flush()

    payu_subscription = create_pending_payu_subscription(
        db,
        user_id=current_user.id,
        plan=plan,
        txnid=txnid,
        amount=amount_str,
        si_details=si_details,
        payu_billing_cycle=billing_cycle,
        payu_billing_interval=billing_interval,
    )
    db.commit()
    db.refresh(payu_subscription)

    backend_base = settings.BACKEND_URL.rstrip('/')
    callback_url = f"{backend_base}/api/payment/callback"

    payment_data = {
        "key": _payu_str(settings.PAYU_MERCHANT_KEY),
        "txnid": txnid,
        "amount": amount_str,
        "productinfo": _payu_str(plan.title) or "Subscription",
        "firstname": _payu_str(user_firstname) or "Customer",
        "email": _payu_str(user_email),
        "phone": _payu_str(req.phone),
        "surl": f"{callback_url}?type=success",
        "furl": f"{callback_url}?type=fail",
        "curl": f"{callback_url}?type=fail",
        "si": "1",
        "si_details": si_details_json,
        "udf1": str(transaction.id),
        "udf2": _payu_str(plan.plan_name).strip(),
        "udf3": str(getattr(plan, "id", "") or ""),
        "udf4": "autopay",
        "udf5": f"{billing_cycle}:{billing_interval}",
    }
    
    payment_data["hash"] = generate_request_hash(payment_data)
    logger.info(
        f"PayU Autopay mandate initiated for User ID {current_user.id} | Plan: {plan.title} | "
        f"Cycle: {billing_cycle}/{billing_interval} | TxnID: {txnid} | "
        f"Ref: {payu_subscription.subscription_reference}"
    )

    return schemas.StandardPaymentResponse(
        status=True,
        msg=f"PayU Autopay subscription initiated successfully for {plan.title}.",
        data={
            "action_url": settings.PAYU_BASE_URL,
            "payment_data": payment_data
        }
    )


@router.post("/callback")
async def payment_callback(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    data_dict = dict(form_data)
    
    txnid = data_dict.get('txnid', '')
    status = data_dict.get('status', '')
    payu_money_id = data_dict.get('mihpayid', '') or data_dict.get('authPayuId', '') or data_dict.get('authpayuid', '')
    raw_action_udf = data_dict.get('udf2', '').strip().lower()
    raw_credits_udf = data_dict.get('udf3', '').strip()
    payment_source = data_dict.get('payment_source', '')
    is_autopay = (data_dict.get('udf4', '') or '').strip().lower() == 'autopay' or str(data_dict.get('si', '')) == '1'

    logger.info(
        f"PayU Callback Triggered | TxnID: {txnid} | Status: {status} | Action UDF2: {raw_action_udf} | "
        f"Autopay: {is_autopay} | payment_source: {payment_source} | mandate: {payu_money_id}"
    )
    frontend_base = settings.FRONTEND_URL.rstrip('/')

    txn = db.query(models.PaymentTransaction).filter(models.PaymentTransaction.txnid == txnid).first()
    if not txn:
        logger.error(f"Callback Error: Transaction ID '{txnid}' not found in database.")
        return RedirectResponse(url=f"{frontend_base}/payment-status?status=failed&reason=invalid_txnid", status_code=303)

    is_valid = validate_response_hash(data_dict)
    txn.raw_response = data_dict
    txn.payu_money_id = payu_money_id

    if not is_valid:
        logger.warning(f"SECURITY ALERT: Hash mismatch for TxnID: {txnid}. Payload tampered or invalid test hash.")
        txn.status = models.TransactionStatus.TAMPERED
        db.commit()
        return RedirectResponse(url=f"{frontend_base}/payment-status?status=failed&reason=hash_mismatch&txnid={txnid}", status_code=303)

    if status == 'success':
        txn.status = models.TransactionStatus.SUCCESS
        
        if txn.user_id:
            user = db.query(models.User).filter(models.User.id == txn.user_id).first()
            now = datetime.utcnow()

            if not user:
                logger.error(f"Subscription Error: User ID {txn.user_id} associated with Txn {txnid} does not exist.")
            
            # ------------------------------------------------------------------
            # BRANCH A: TOP-UP TRANSACTION PROCESSING
            # ------------------------------------------------------------------
            elif raw_action_udf == "topup":
                try:
                    added_credits = int(raw_credits_udf)
                except ValueError:
                    added_credits = 10  # Fallback

                active_sub = db.query(models.UserSubscription).filter(
                    models.UserSubscription.user_id == user.id,
                    models.UserSubscription.status == models.UserSubscriptionStatus.ACTIVE
                ).first()

                if not active_sub:
                    from .gatekeeper import auto_provision_free_tier
                    active_sub = auto_provision_free_tier(db, user.id)

                active_sub.credits_remaining += added_credits
                active_sub.latest_txnid = txnid
                active_sub.latest_payment_amount = str(txn.amount)
                active_sub.latest_payment_date = now

                db.add(models.UserResourceUsageLog(
                    user_id=user.id,
                    user_subscription_id=active_sub.id,
                    resource_key=models.ResourceKey.CREDITS,
                    delta=added_credits,
                    used_after=active_sub.credits_remaining,
                    reference_type="topup",
                    reference_id=txn.id,
                    description=f"Top-Up Recharge: +{added_credits} Credits (₹{txn.amount})"
                ))

                logger.info(f"TOP-UP SUCCESS: Added {added_credits} credits to User ID {user.id}. Balance: {active_sub.credits_remaining}")

            # ------------------------------------------------------------------
            # BRANCH B: SUBSCRIPTION PLAN PURCHASE PROCESSING
            # ------------------------------------------------------------------
            else:
                plan = None
                plan_id_udf = (data_dict.get('udf3') or '').strip()
                if plan_id_udf.isdigit():
                    plan = db.query(models.SubscriptionPlan).filter(
                        models.SubscriptionPlan.id == int(plan_id_udf),
                        models.SubscriptionPlan.is_active == True
                    ).first()

                if not plan:
                    plan = db.query(models.SubscriptionPlan).filter(
                        (func.lower(func.trim(models.SubscriptionPlan.plan_name)) == raw_action_udf) |
                        (func.lower(func.trim(models.SubscriptionPlan.title)).like(f"%{raw_action_udf}%")),
                        models.SubscriptionPlan.is_active == True
                    ).first()

                if not plan:
                    logger.error(f"Subscription Error: No active plan found matching UDF2 string '{raw_action_udf}'.")
                else:
                    billing_cycle, billing_interval = resolve_payu_billing(plan)
                    logger.info(
                        f"Processing Autopay subscription for User ID {user.id} | Plan: {plan.title} | "
                        f"Mandate: {payu_money_id} | Cycle: {billing_cycle}/{billing_interval}"
                    )

                    snapshot = {
                        "subscription_plan_id": plan.id,
                        "plan_name": plan.plan_name.strip(),
                        "title": plan.title,
                        "price": float(plan.price) if plan.price else 0,
                        "credits": plan.credits,
                        "billing_cycle": billing_cycle,
                        "billing_interval": billing_interval,
                        "autopay": True,
                        "payu_mandate_id": payu_money_id,
                        "closet_limit": getattr(plan, "closet_limit", 10),
                        "virtual_try_on": getattr(plan, "virtual_try_on", True),
                        "view_360_mode": extract_enum_or_val(plan, "view_360_mode", "single_image"),
                        "change_background": getattr(plan, "change_background", True),
                        "model_swap": getattr(plan, "model_swap", False),
                        "product_to_model": getattr(plan, "product_to_model", True),
                        "outerwear_enabled": getattr(plan, "outerwear_enabled", False),
                        "image_to_video_resolution": extract_enum_or_val(plan, "image_to_video_resolution", "720p"),
                        "image_to_video_max_count": getattr(plan, "image_to_video_max_count", None),
                        "image_to_video_max_seconds": getattr(plan, "image_to_video_max_seconds", 10),
                        "smart_crop": getattr(plan, "smart_crop", True),
                        "face_to_model": getattr(plan, "face_to_model", False),
                        "create_model_enabled": getattr(plan, "create_model_enabled", False),
                        "create_model_max": getattr(plan, "create_model_max", None),
                        "video_quality": extract_enum_or_val(plan, "video_quality", "480p"),
                        "chat_support_enabled": getattr(plan, "chat_support_enabled", False),
                        "chat_support_response_hours": (
                            float(plan.chat_support_response_hours)
                            if getattr(plan, "chat_support_response_hours", None) is not None
                            else None
                        ),
                        "model_creation_limit": getattr(plan, "model_creation_limit", getattr(plan, "create_model_max", None)),
                        "special_offer": getattr(plan, "special_offer", False),
                        "early_access": getattr(plan, "early_access", False),
                        "image_quality": extract_enum_or_val(plan, "image_quality", "2k"),
                        "image_retention_hours": getattr(plan, "image_retention_hours", 24),
                    }

                    cycle_end = add_billing_period(now, billing_cycle, billing_interval)

                    active_sub = db.query(models.UserSubscription).filter(
                        models.UserSubscription.user_id == user.id,
                        models.UserSubscription.status == models.UserSubscriptionStatus.ACTIVE
                    ).first()

                    if active_sub:
                        history_event = models.SubscriptionEvent.UPGRADED
                        active_sub.plan_snapshot = snapshot
                        active_sub.credits_remaining += plan.credits
                        active_sub.subscription_plan_id = plan.id
                        active_sub.starts_at = now
                        active_sub.ends_at = cycle_end
                        active_sub.latest_txnid = txnid
                        active_sub.latest_payment_amount = str(plan.price)
                        active_sub.latest_payment_date = now
                        sub_id = active_sub.id
                        logger.info(f"Upgraded User ID {user.id} active sub. Added {plan.credits} credits.")
                    else:
                        history_event = models.SubscriptionEvent.ASSIGNED
                        new_sub = models.UserSubscription(
                            user_id=user.id,
                            subscription_plan_id=plan.id,
                            plan_snapshot=snapshot,
                            credits_remaining=plan.credits,
                            status=models.UserSubscriptionStatus.ACTIVE,
                            starts_at=now,
                            ends_at=cycle_end,
                            latest_txnid=txnid,
                            latest_payment_amount=str(plan.price),
                            latest_payment_date=now
                        )
                        db.add(new_sub)
                        db.flush()
                        sub_id = new_sub.id
                        logger.info(f"Created new active subscription ID {sub_id} for User ID {user.id} with {plan.credits} credits.")

                    # Quota Initialization
                    model_limit = snapshot.get("model_creation_limit")
                    model_usage = db.query(models.UserPlanResourceUsage).filter(
                        models.UserPlanResourceUsage.user_subscription_id == sub_id,
                        models.UserPlanResourceUsage.resource_key == models.ResourceKey.MODEL_CREATION
                    ).first()
                    if model_usage:
                        model_usage.limit_value = model_limit
                        model_usage.period_starts_at = now
                        model_usage.period_ends_at = cycle_end
                    else:
                        db.add(models.UserPlanResourceUsage(
                            user_id=user.id,
                            user_subscription_id=sub_id,
                            resource_key=models.ResourceKey.MODEL_CREATION,
                            limit_value=model_limit,
                            used_value=0,
                            period_starts_at=now,
                            period_ends_at=cycle_end
                        ))

                    video_limit = snapshot.get("image_to_video_max_count")
                    video_usage = db.query(models.UserPlanResourceUsage).filter(
                        models.UserPlanResourceUsage.user_subscription_id == sub_id,
                        models.UserPlanResourceUsage.resource_key == models.ResourceKey.IMAGE_TO_VIDEO
                    ).first()
                    if video_usage:
                        video_usage.limit_value = video_limit
                        video_usage.period_starts_at = now
                        video_usage.period_ends_at = cycle_end
                    else:
                        db.add(models.UserPlanResourceUsage(
                            user_id=user.id,
                            user_subscription_id=sub_id,
                            resource_key=models.ResourceKey.IMAGE_TO_VIDEO,
                            limit_value=video_limit,
                            used_value=0,
                            period_starts_at=now,
                            period_ends_at=cycle_end
                        ))

                    db.add(models.UserSubscriptionHistory(
                        user_id=user.id,
                        user_subscription_id=sub_id,
                        subscription_plan_id=plan.id,
                        event=history_event,
                        plan_snapshot=snapshot,
                        credits_at_event=plan.credits
                    ))

                    save_payu_subscription_on_success(
                        db,
                        user_id=user.id,
                        plan=plan,
                        user_subscription_id=sub_id,
                        txnid=txnid,
                        amount=txn.amount,
                        mandate_id=payu_money_id,
                        payu_billing_cycle=billing_cycle,
                        payu_billing_interval=billing_interval,
                        cycle_end=cycle_end,
                        payload=data_dict,
                        now=now,
                    )

        db.commit()
        logger.info(f"PayU Autopay transaction {txnid} finalized successfully. Mandate: {payu_money_id}")
        return RedirectResponse(url=f"{frontend_base}/payment-status?status=success&txnid={txnid}", status_code=303)
    else:
        txn.status = models.TransactionStatus.FAILED
        if is_autopay or (txnid or "").startswith("VTONSI"):
            save_payu_subscription_on_failure(
                db,
                txnid=txnid,
                mandate_id=payu_money_id,
                payload=data_dict,
                now=datetime.utcnow(),
                amount=txn.amount,
            )
        db.commit()
        logger.warning(f"Transaction {txnid} processed with non-success status: '{status}'")
        return RedirectResponse(url=f"{frontend_base}/payment-status?status=failed&txnid={txnid}", status_code=303)


# ==============================================================================
# GET PAYMENT HISTORY
# ==============================================================================
@router.get("/payment-history", response_model=schemas.PaymentHistoryListResponse)
async def get_payment_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Fetches the user's successful payment transactions and enriches them 
    with plan details, validation dates, and allocated credits.
    """
    try:
        transactions = db.query(models.PaymentTransaction).filter(
            models.PaymentTransaction.user_id == current_user.id,
            models.PaymentTransaction.status == models.TransactionStatus.SUCCESS
        ).order_by(models.PaymentTransaction.created_at.desc()).all()

        plans_cache = {plan.title: plan for plan in db.query(models.SubscriptionPlan).all()}

        history_list = []
        for txn in transactions:
            purchase_dt = txn.created_at
            matched_plan = plans_cache.get(txn.product_info)

            if matched_plan and purchase_dt:
                cycle, interval = resolve_payu_billing(matched_plan)
                validation_dt = add_billing_period(purchase_dt, cycle, interval)
            else:
                validation_dt = purchase_dt + timedelta(days=30) if purchase_dt else None
            
            formatted_purchase = purchase_dt.strftime("%b %d %Y") if purchase_dt else "N/A"
            formatted_validation = validation_dt.strftime("%b %d %Y") if validation_dt else "N/A"
            
            # Map credits accurately for both plan purchases and top-ups
            if matched_plan:
                credits_purchased = matched_plan.credits
            elif txn.product_info and "Top-Up" in txn.product_info:
                match = re.search(r"\d+", txn.product_info)
                credits_purchased = int(match.group()) if match else 0
            else:
                credits_purchased = 0

            history_list.append({
                "transaction_id": txn.txnid,
                "plan_name": txn.product_info,
                "purchase_amount": txn.amount,
                "purchase_date": formatted_purchase,
                "validation_date": formatted_validation,
                "credits_purchased": credits_purchased
            })

        return schemas.PaymentHistoryListResponse(
            status=True,
            msg="Payment history retrieved successfully.",
            data=history_list
        )

    except Exception as e:
        logger.error(f"Error fetching payment history for user {current_user.id}: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to retrieve payment history.")


# ==============================================================================
# CREDITS TOP-UP APIS
# ==============================================================================

@router.get(
    "/topup-options",
    response_model=schemas.StandardTopupOptionsResponse
)
async def get_topup_options(
    db: Session = Depends(get_db)
):
    """
    Returns available credit top-up packages for the frontend pricing modal.
    """

    options = (
        db.query(models.TopupOption)
        .order_by(models.TopupOption.id.asc())
        .all()
    )

    data = [
        {
            "id": option.id,
            "title": option.title,
            "credits": option.credits,
            "amount": float(option.amount),
            "description": option.description,
        }
        for option in options
    ]

    return schemas.StandardTopupOptionsResponse(
        status=True,
        msg="Top-up packages retrieved successfully.",
        credit_rate=20,
        data=data
    )

@router.post("/topup/initiate", response_model=schemas.StandardPaymentResponse)
async def initiate_topup_payment(
    req: schemas.PaymentTopupInitiateRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Initiates a PayU payment transaction specifically for credit top-ups.
    Encodes metadata: udf2='topup', udf3=credits_count.
    """
    
    # --- PRE-FLIGHT CHECK: Verify Fashn Master Credits vs Top-up Credits ---
    fashn_balance = await check_fashn_master_balance()
    if fashn_balance >= 0 and fashn_balance < req.credits:
        logger.critical(
            f"[TOPUP INITIATE BLOCKED] Fashn master balance ({fashn_balance}) is less than requested topup credits ({req.credits})."
        )
        raise APIException(
            status_code=200,
            msg="Credit top-ups are temporarily paused due to upstream maintenance. Please try again shortly."
        )
    user_firstname = current_user.full_name or current_user.username
    user_email = current_user.email
    
    txnid = f"TOPUP{int(time.time())}{random.randint(1000, 9999)}"
    product_info = f"Top-Up {req.credits} Credits"
    amount_str = f"{req.amount:.2f}"

    transaction = models.PaymentTransaction(
        txnid=txnid,
        user_id=current_user.id,
        amount=amount_str,           
        product_info=product_info,     
        firstname=user_firstname,
        email=user_email,
        # phone=req.phone,             
        status=models.TransactionStatus.PENDING
    )
    db.add(transaction)
    db.commit()

    backend_base = settings.BACKEND_URL.rstrip('/')

    payment_data = {
        "key": settings.PAYU_MERCHANT_KEY,
        "txnid": txnid,
        "amount": amount_str,
        "productinfo": product_info,
        "firstname": user_firstname,
        "email": user_email,
        # "phone": req.phone,
        "surl": f"{backend_base}/api/payment/callback?type=success", 
        "furl": f"{backend_base}/api/payment/callback?type=fail",
        "udf1": str(transaction.id),
        "udf2": "topup",           
        "udf3": str(req.credits),  
        "udf4": "",
        "udf5": ""
    }
    
    payment_data["hash"] = generate_request_hash(payment_data)
    logger.info(f"Top-up payment initiated for User ID {current_user.id} | Credits: {req.credits} | Amount: ₹{amount_str} | TxnID: {txnid}")

    return schemas.StandardPaymentResponse(
        status=True,
        msg=f"Top-up transaction initiated successfully for {req.credits} credits.",
        data={
            "action_url": settings.PAYU_BASE_URL,
            "payment_data": payment_data
        }
    )