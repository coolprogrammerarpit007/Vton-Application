import hashlib
import time
import random
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
        raise APIException(status_code=400, msg=f"Invalid or inactive subscription plan '{req.plan_name}' selected.")
    
    user_firstname = current_user.full_name or current_user.username
    user_email = current_user.email
    
    txnid = f"VTON{int(time.time())}{random.randint(1000, 9999)}"
    
    transaction = models.PaymentTransaction(
        txnid=txnid,
        user_id=current_user.id,
        amount=plan.price,           
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
        "amount": plan.price,
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
    raw_plan_udf = data_dict.get('udf2', '').strip().lower()

    logger.info(f"PayU Callback Triggered | TxnID: {txnid} | Status: {status} | Plan UDF: {raw_plan_udf}")
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
            plan = db.query(models.SubscriptionPlan).filter(
                (func.lower(func.trim(models.SubscriptionPlan.plan_name)) == raw_plan_udf) |
                (func.lower(func.trim(models.SubscriptionPlan.title)).like(f"%{raw_plan_udf}%")),
                models.SubscriptionPlan.is_active == True
            ).first()

            if not user:
                logger.error(f"Subscription Error: User ID {txn.user_id} associated with Txn {txnid} does not exist.")
            elif not plan:
                logger.error(f"Subscription Error: No active plan found matching UDF2 string '{raw_plan_udf}'.")
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

                # Define Billing Cycle
                now = datetime.utcnow()
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
                        ends_at=cycle_end
                    )
                    db.add(new_sub)
                    db.flush()
                    sub_id = new_sub.id
                    logger.info(f"Created new active subscription ID {sub_id} for User ID {user.id} with {plan.credits} credits.")

                # Quota Initialization with Periods
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