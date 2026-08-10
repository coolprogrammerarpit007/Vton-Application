import logging
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from . import models, schemas
from .database import get_db
from .exceptions import APIException
from .auth import get_current_user  # NEW: Import the auth dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plans", tags=["Subscription Plans"])

@router.get("", response_model=schemas.StandardSubscriptionPlanListResponse)
async def get_all_subscription_plans(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)  # NEW: Require logged-in user
):
    """
    Fetches all active subscription plans and checks if the user has billing details.
    """
    try:
        # 1. Fetch active plans
        plans = db.query(models.SubscriptionPlan).filter(
            models.SubscriptionPlan.is_active == True
        ).all()

        # 2. Check if the user has a billing record
        has_billing = False
        billing_record = db.query(models.UserBillingDetail).filter(
            models.UserBillingDetail.user_id == current_user.id
        ).first()
        
        if billing_record:
            has_billing = True

        # 3. Return combined response
        return schemas.StandardSubscriptionPlanListResponse(
            status=True,
            msg="Subscription plans retrieved successfully.",
            has_billing_details=has_billing,  # NEW: Inject the boolean flag here
            data=plans
        )
        
    except Exception as e:
        logger.error(f"Error fetching subscription plans: {str(e)}")
        raise APIException(status_code=500, msg="Failed to fetch subscription plans.")


# @router.get("/{plan_name}", response_model=schemas.StandardResponse)
# async def get_subscription_plan_by_name(plan_name: str, db: Session = Depends(get_db)):
#     """
#     Fetches details for a single plan (e.g. /api/plans/gold).
#     """
#     plan = db.query(models.SubscriptionPlan).filter(
#         models.SubscriptionPlan.plan_name == plan_name.lower(),
#         models.SubscriptionPlan.is_active == True
#     ).first()

#     if not plan:
#         raise APIException(status_code=404, msg="Subscription plan not found.")

#     plan_dict = {
#         "id": plan.id,
#         "plan_name": plan.plan_name,
#         "title": plan.title,
#         "price": plan.price,
#         "credits": plan.credits,
#         "is_active": plan.is_active
#     }

#     return schemas.StandardResponse(
#         status=True,
#         msg="Subscription plan details retrieved successfully.",
#         data=plan_dict
#     )
    
    
    
# ************************************* My Plans API ********************************************


def extract_enum_or_val(obj, attr_name: str, default=None):
    """Safely extracts field value, handling SQLAlchemy Enum objects for JSON serialization."""
    val = getattr(obj, attr_name, default)
    if hasattr(val, "value"):
        return val.value
    return val


def build_free_plan_snapshot(db: Session) -> tuple[dict, int]:
    """
    Fetches the active Silver plan from the DB to inherit its feature limits,
    overriding the credits to 3 for the Free Tier.
    """
    silver_plan = db.query(models.SubscriptionPlan).filter(
        (func.lower(func.trim(models.SubscriptionPlan.plan_name)).like("%silver%")) |
        (func.lower(func.trim(models.SubscriptionPlan.title)).like("%silver%")),
        models.SubscriptionPlan.is_active == True
    ).first()

    if silver_plan:
        snapshot = {
            "subscription_plan_id": silver_plan.id,
            "plan_name": "Free",
            "title": "Free Plan",
            "price": 0.0,
            "credits": 3, # Free tier gets 3 initial credits
            "closet_limit": getattr(silver_plan, "closet_limit", 10),
            "virtual_try_on": getattr(silver_plan, "virtual_try_on", True),
            "view_360_mode": extract_enum_or_val(silver_plan, "view_360_mode", "single_image"),
            "change_background": getattr(silver_plan, "change_background", True),
            "model_swap": getattr(silver_plan, "model_swap", False),
            "product_to_model": getattr(silver_plan, "product_to_model", True),
            "outerwear_enabled": getattr(silver_plan, "outerwear_enabled", False),
            "image_to_video_resolution": extract_enum_or_val(silver_plan, "image_to_video_resolution", "480p"),
            "image_to_video_max_count": getattr(silver_plan, "image_to_video_max_count", 1),
            "image_to_video_max_seconds": getattr(silver_plan, "image_to_video_max_seconds", 10),
            "smart_crop": getattr(silver_plan, "smart_crop", True),
            "face_to_model": getattr(silver_plan, "face_to_model", False),
            "create_model_enabled": getattr(silver_plan, "create_model_enabled", False),
            "create_model_max": getattr(silver_plan, "create_model_max", None),
            "video_quality": extract_enum_or_val(silver_plan, "video_quality", "480p"),
            "chat_support_enabled": getattr(silver_plan, "chat_support_enabled", False),
            "chat_support_response_hours": None,
            "model_creation_limit": None,
            "special_offer": False,
            "early_access": False,
            "image_quality": extract_enum_or_val(silver_plan, "image_quality", "2k"),
            "image_retention_hours": getattr(silver_plan, "image_retention_hours", 24),
        }
        plan_id = silver_plan.id
    else:
        # Hardcoded fallback if Silver plan is not configured in DB
        snapshot = {
            "subscription_plan_id": 1,
            "plan_name": "Free",
            "title": "Free Plan",
            "price": 0.0,
            "credits": 3,
            "closet_limit": 2,
            "virtual_try_on": True,
            "view_360_mode": "single_image",
            "change_background": True,
            "model_swap": False,
            "product_to_model": True,
            "outerwear_enabled": False,
            "image_to_video_resolution": "480p",
            "image_to_video_max_count": 1,
            "image_to_video_max_seconds": 10,
            "smart_crop": True,
            "face_to_model": False,
            "create_model_enabled": False,
            "create_model_max": None,
            "video_quality": "480p",
            "chat_support_enabled": False,
            "chat_support_response_hours": None,
            "model_creation_limit": None,
            "special_offer": False,
            "early_access": False,
            "image_quality": "2k",
            "image_retention_hours": 24,
        }
        plan_id = 1

    return snapshot, plan_id


# ==============================================================================
# 1. GET CURRENT LOGGED-IN USER PLAN DETAILS (FREE OR PAID)
# ==============================================================================
@router.get("/my-plan", response_model=schemas.StandardResponse)
async def get_user_current_plan(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Returns plan details for the logged-in user.
    If the user has not purchased a plan, returns Free Tier details 
    (Silver plan features with 3 initial credits).
    """
    try:
        active_sub = db.query(models.UserSubscription).filter(
            models.UserSubscription.user_id == current_user.id,
            models.UserSubscription.status == models.UserSubscriptionStatus.ACTIVE
        ).first()

        if active_sub:
            snapshot = active_sub.plan_snapshot or {}
            is_free = snapshot.get("plan_name", "").lower() == "free"
            
            plan_data = {
                "user_id" : current_user.username,
                "email":current_user.email,
                "is_free_user": is_free,
                "subscription_id": active_sub.id,
                "plan_name": snapshot.get("plan_name", "Active Plan"),
                "title": snapshot.get("title", "Active Plan"),
                "price": snapshot.get("price", 0.0),
                "credits_total": snapshot.get("credits", 0),
                "credits_remaining": active_sub.credits_remaining,
                "status": active_sub.status.value if hasattr(active_sub.status, "value") else str(active_sub.status),
                "starts_at": active_sub.starts_at.isoformat() if active_sub.starts_at else None,
                "ends_at": active_sub.ends_at.isoformat() if active_sub.ends_at else None,
                "latest_txnid": active_sub.latest_txnid,
                "latest_payment_amount": active_sub.latest_payment_amount,
                "latest_payment_date": active_sub.latest_payment_date.isoformat() if active_sub.latest_payment_date else None,
                "features": snapshot
            }
        else:
            # First-time / Free user
            snapshot, _ = build_free_plan_snapshot(db)
            plan_data = {
                "user_id" : current_user.username,
                "email":current_user.email,
                "is_free_user": True,
                "subscription_id": None,
                "plan_name": "Free",
                "title": "Free Plan",
                "price": 0.0,
                "credits_total": 3,
                "credits_remaining": 3,
                "status": "active",
                "starts_at": None,
                "ends_at": None,
                "latest_txnid": None,
                "latest_payment_amount": None,
                "latest_payment_date": None,
                "features": snapshot
            }

        return schemas.StandardResponse(
            status=True,
            msg="User plan details retrieved successfully.",
            data=plan_data
        )

    except Exception as e:
        logger.error(f"Error fetching plan details for user {current_user.id}: {str(e)}", exc_info=True)
        raise APIException(status_code=500, msg="Failed to retrieve plan details.")