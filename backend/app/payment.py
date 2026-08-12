import hashlib
import time
import random
import re
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from . import models, schemas
from .database import get_db
from .config import settings
from .exceptions import APIException
from .auth import get_current_user
from .fashn_service import check_fashn_master_balance


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

router = APIRouter(prefix="/api/payment", tags=["Payment Integration"])

def generate_request_hash(data: dict) -> str:
    hash_sequence = (
        f"{settings.PAYU_MERCHANT_KEY}|{data.get('txnid', '')}|{data.get('amount', '')}|"
        f"{data.get('productinfo', '')}|{data.get('firstname', '')}|{data.get('email', '')}|"
        f"{data.get('udf1', '')}|{data.get('udf2', '')}|{data.get('udf3', '')}|"
        f"{data.get('udf4', '')}|{data.get('udf5', '')}||||||{settings.PAYU_MERCHANT_SALT}"
    )
    return hashlib.sha512(hash_sequence.encode('utf-8')).hexdigest().lower()

def validate_response_hash(data: dict) -> bool:
    received_hash = data.get('hash', '')
    status = data.get('status', '')
    
    hash_sequence = (
        f"{settings.PAYU_MERCHANT_SALT}|{status}||||||"
        f"{data.get('udf5', '')}|{data.get('udf4', '')}|{data.get('udf3', '')}|"
        f"{data.get('udf2', '')}|{data.get('udf1', '')}|{data.get('email', '')}|"
        f"{data.get('firstname', '')}|{data.get('productinfo', '')}|"
        f"{data.get('amount', '')}|{data.get('txnid', '')}|{settings.PAYU_MERCHANT_KEY}"
    )
    calculated_hash = hashlib.sha512(hash_sequence.encode('utf-8')).hexdigest().lower()
    return calculated_hash == received_hash.lower()


def extract_enum_or_val(obj, attr_name: str, default=None):
    val = getattr(obj, attr_name, default)
    if hasattr(val, "value"):
        return val.value
    return val


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
    
    # --- PRE-FLIGHT CHECK: Verify Fashn Master Credits vs Plan Credits ---
    plan_required_credits = plan.credits or 0
    fashn_balance = await check_fashn_master_balance()
    
    if fashn_balance >= 0 and fashn_balance < plan_required_credits:
        logger.critical(
            f"[PAYMENT INITIATE BLOCKED] Fashn master balance ({fashn_balance}) is less than plan credits ({plan_required_credits})."
        )
        raise APIException(
            status_code=200,
            msg="Subscription purchases are temporarily paused due to upstream maintenance. Please try again shortly."
        )
    
    user_firstname = current_user.full_name or current_user.username
    user_email = current_user.email
    
    txnid = f"VTON{int(time.time())}{random.randint(1000, 9999)}"
    
    transaction = models.PaymentTransaction(
        txnid=txnid,
        user_id=current_user.id,
        amount=plan.total_price,           
        product_info=plan.title,     
        firstname=user_firstname,
        email=user_email,
        phone=req.phone,             
        status=models.TransactionStatus.PENDING
    )
    db.add(transaction)
    db.commit()

    backend_base = settings.BACKEND_URL.rstrip('/')

    payment_data = {
        "key": settings.PAYU_MERCHANT_KEY,
        "txnid": txnid,
        "amount": plan.total_price,
        "productinfo": plan.title,
        "firstname": user_firstname,
        "email": user_email,
        "phone": req.phone,
        "surl": f"{backend_base}/api/payment/callback?type=success", 
        "furl": f"{backend_base}/api/payment/callback?type=fail",
        "udf1": str(transaction.id),
        "udf2": plan.plan_name.strip(),  
        "udf3": "",
        "udf4": "",
        "udf5": ""
    }
    
    payment_data["hash"] = generate_request_hash(payment_data)
    logger.info(f"Payment initiated for User ID {current_user.id} | Plan: {plan.title} | TxnID: {txnid}")

    return schemas.StandardPaymentResponse(
        status=True,
        msg=f"Payment transaction initiated successfully for {plan.title}.",
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
    payu_money_id = data_dict.get('mihpayid', '')
    raw_action_udf = data_dict.get('udf2', '').strip().lower()
    raw_credits_udf = data_dict.get('udf3', '').strip()

    logger.info(f"PayU Callback Triggered | TxnID: {txnid} | Status: {status} | Action UDF2: {raw_action_udf}")
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
                plan = db.query(models.SubscriptionPlan).filter(
                    (func.lower(func.trim(models.SubscriptionPlan.plan_name)) == raw_action_udf) |
                    (func.lower(func.trim(models.SubscriptionPlan.title)).like(f"%{raw_action_udf}%")),
                    models.SubscriptionPlan.is_active == True
                ).first()

                if not plan:
                    logger.error(f"Subscription Error: No active plan found matching UDF2 string '{raw_action_udf}'.")
                else:
                    logger.info(f"Processing subscription allocation for User ID {user.id} | Plan: {plan.title}")

                    snapshot = {
                        "subscription_plan_id": plan.id,
                        "plan_name": plan.plan_name.strip(),
                        "title": plan.title,
                        "price": float(plan.price) if plan.price else 0,
                        "credits": plan.credits,
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

                    cycle_end = now + timedelta(days=30)

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

        db.commit()
        logger.info(f"Transaction {txnid} finalized successfully.")
        return RedirectResponse(url=f"{frontend_base}/payment-status?status=success&txnid={txnid}", status_code=303)
    else:
        txn.status = models.TransactionStatus.FAILED
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

        plans_cache = {plan.title: plan.credits for plan in db.query(models.SubscriptionPlan).all()}

        history_list = []
        for txn in transactions:
            purchase_dt = txn.created_at
            validation_dt = purchase_dt + timedelta(days=30) if purchase_dt else None
            
            formatted_purchase = purchase_dt.strftime("%b %d %Y") if purchase_dt else "N/A"
            formatted_validation = validation_dt.strftime("%b %d %Y") if validation_dt else "N/A"
            
            # Map credits accurately for both plan purchases and top-ups
            if txn.product_info in plans_cache:
                credits_purchased = plans_cache[txn.product_info]
            elif "Top-Up" in txn.product_info:
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