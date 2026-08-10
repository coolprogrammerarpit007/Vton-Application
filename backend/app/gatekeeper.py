import logging
from typing import Optional, Dict, Any
from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from . import models
from .database import get_db
from .auth import get_current_user
from .exceptions import APIException

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta

def extract_enum_or_val(obj, attr_name: str, default=None):
    val = getattr(obj, attr_name, default)
    if hasattr(val, "value"):
        return val.value
    return val


def auto_provision_free_tier(db: Session, user_id: int) -> models.UserSubscription:
    """
    Provisions a Free Tier Subscription (3 credits, Silver features) 
    in the database for first-time users.
    """
    silver_plan = db.query(models.SubscriptionPlan).filter(
        (func.lower(func.trim(models.SubscriptionPlan.plan_name)).like("%silver%")) |
        (func.lower(func.trim(models.SubscriptionPlan.title)).like("%silver%")),
        models.SubscriptionPlan.is_active == True
    ).first()

    if silver_plan:
        plan_id = silver_plan.id
        snapshot = {
            "subscription_plan_id": silver_plan.id,
            "plan_name": "Free",
            "title": "Free Plan",
            "price": 0.0,
            "credits": 3,
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
    else:
        plan_id = 1
        snapshot = {
            "subscription_plan_id": 1,
            "plan_name": "Free",
            "title": "Free Plan",
            "price": 0.0,
            "credits": 3,
            "closet_limit": 10,
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

    # 1. Define the 30-day cycle for the Free Tier
    now = datetime.utcnow()
    cycle_end = now + timedelta(days=30)
    
    free_sub = models.UserSubscription(
        user_id=user_id,
        subscription_plan_id=plan_id,
        plan_snapshot=snapshot,
        credits_remaining=3,
        status=models.UserSubscriptionStatus.ACTIVE,
        starts_at=now,
        ends_at=cycle_end,
        notes="Auto-assigned Free Tier (3 credits) on first feature usage"
    )
    
    db.add(free_sub)
    db.flush() # Flush to generate the free_sub.id needed for the usage tables
    
    # 2. Initialize Volume Quota for Model Creation
    model_limit = snapshot.get("model_creation_limit")
    db.add(models.UserPlanResourceUsage(
        user_id=user_id,
        user_subscription_id=free_sub.id,
        resource_key=models.ResourceKey.MODEL_CREATION,
        limit_value=model_limit,
        used_value=0,
        period_starts_at=now,
        period_ends_at=cycle_end
    ))

    # 3. Initialize Volume Quota for Image-to-Video
    video_limit = snapshot.get("image_to_video_max_count")
    db.add(models.UserPlanResourceUsage(
        user_id=user_id,
        user_subscription_id=free_sub.id,
        resource_key=models.ResourceKey.IMAGE_TO_VIDEO,
        limit_value=video_limit,
        used_value=0,
        period_starts_at=now,
        period_ends_at=cycle_end
    ))

    # 4. Write the Assignment Log to History
    db.add(models.UserSubscriptionHistory(
        user_id=user_id,
        user_subscription_id=free_sub.id,
        subscription_plan_id=plan_id,
        event=models.SubscriptionEvent.ASSIGNED,
        plan_snapshot=snapshot,
        credits_at_event=3
    ))
    try:
        db.commit()
        db.refresh(free_sub)
        logger.info(f"Auto-provisioned Free Tier (3 credits) for User ID {user_id}")
        return free_sub
    except Exception:
        db.rollback()
        # Fallback query in case of concurrent insert
        return db.query(models.UserSubscription).filter(
            models.UserSubscription.user_id == user_id,
            models.UserSubscription.status == models.UserSubscriptionStatus.ACTIVE
        ).first()
class PlanGatekeeper:
    """
    FastAPI Dependency to enforce Subscription Plan rules, Feature Flags, and Volume Limits.
    
    Expected Result: 
    Intercepts an API request, validates the user has an ACTIVE subscription, and checks 
    their immutable `plan_snapshot` for feature access. If a `resource_key` is provided, 
    it queries `UserPlanResourceUsage` to ensure the user has not hit their volume cap 
    (e.g., maximum 15 AI models on the Gold plan). Returns the subscription object if authorized.
    """
    def __init__(
        self,
        feature_flag: Optional[str] = None,
        min_image_quality: Optional[str] = None,
        min_video_quality: Optional[str] = None,
        resource_key: Optional[models.ResourceKey] = None 
    ):
        self.feature_flag = feature_flag
        self.min_image_quality = min_image_quality
        self.min_video_quality = min_video_quality
        self.resource_key = resource_key 

    def __call__(
        self,
        current_user: models.User = Depends(get_current_user),
        db: Session = Depends(get_db)
    ) -> models.UserSubscription:
        
        # 1. Fetch Active Subscription
        subscription = db.query(models.UserSubscription).filter(
            models.UserSubscription.user_id == current_user.id,
            models.UserSubscription.status == models.UserSubscriptionStatus.ACTIVE
        ).first()
        
        
        # Auto-provision Free Tier for first-time users
        if not subscription:
            subscription = auto_provision_free_tier(db, current_user.id)

        snapshot = subscription.plan_snapshot
        # if not subscription:
        #     logger.warning(f"Gatekeeper Block: User {current_user.id} has no active subscription.")
        #     raise APIException(
        #         status_code=200,
        #         msg="No active subscription found. Please purchase a plan to continue."
        #     )

        snapshot = subscription.plan_snapshot

       
        # 2. Check Boolean Feature Flag Access
        if self.feature_flag:
            has_access = snapshot.get(self.feature_flag, False)
            if not has_access:
                # Map raw database column names to clean, user-friendly UI strings
                feature_name_map = {
                    "create_model_enabled": "AI Model Creation",
                    "model_swap": "Model Swap",
                    "face_to_model": "Face to Model",
                    "product_to_model": "Product to Model",
                    "change_background": "Background Replacement",
                    "outerwear_enabled": "Outerwear Try-On"
                }
                friendly_name = feature_name_map.get(self.feature_flag, self.feature_flag.replace("_", " ").title())
                
                logger.warning(f"Gatekeeper Block: User {current_user.id} attempted to access restricted feature '{friendly_name}'.")
                raise APIException(
                    status_code=200,
                    msg=f"Your current plan does not support the {friendly_name} feature. Please upgrade to unlock this."
                )

        # 3. Validate Requested Image Resolution (2K vs 4K)
        if self.min_image_quality:
            user_image_quality = snapshot.get("image_quality", "2k")
            if self.min_image_quality == "4k" and user_image_quality == "2k":
                logger.warning(f"Gatekeeper Block: User {current_user.id} attempted 4K generation on 2K plan.")
                raise APIException(
                    status_code=200,
                    msg="4K render quality requires the Gold or Platinum plan."
                )

        quality_map = {"480p": 1, "720p": 2, "1080p": 3}
        if self.min_video_quality:
            user_video_quality = snapshot.get("video_quality", "480p")
            user_max = quality_map.get(user_video_quality, 1)
            req_min = quality_map.get(self.min_video_quality, 1)
            
            if req_min > user_max:
                logger.warning(f"Gatekeeper Block: User {current_user.id} attempted {self.min_video_quality} video on {user_video_quality} plan.")
                raise APIException(
                    status_code=200,
                    msg=f"Your plan is limited to {user_video_quality} video exports."
                )

        # 5. Check Resource Usage Limits (Volume Caps from user_plan_resource_usages)
        if self.resource_key:
            usage_record = db.query(models.UserPlanResourceUsage).filter(
                models.UserPlanResourceUsage.user_subscription_id == subscription.id,
                models.UserPlanResourceUsage.resource_key == self.resource_key
            ).first()

            if usage_record and usage_record.limit_value is not None:
                if usage_record.used_value >= usage_record.limit_value:
                    logger.warning(f"Gatekeeper Block: User {current_user.id} exhausted {self.resource_key.value} limit ({usage_record.limit_value}).")
                    raise APIException(
                        status_code=200,
                        msg=f"You have reached your limit for {self.resource_key.value}. Limit: {usage_record.limit_value}."
                    )

        return subscription


class SubscriptionTransactionManager:
    """
    Utility class to handle the atomic deduction and refund of credits AND volume quotas.
    Writes directly to the immutable `user_resource_usage_logs` ledger.
    """
    
    @staticmethod
    def calculate_cost(task_type: str, snapshot: Dict[str, Any], params: Dict[str, Any] = {}) -> int:
        """
        Expected Result: Returns the exact integer credit cost based on the user's plan tier 
        and the specific parameters requested (e.g., 4K vs 2K).
        """
        plan_name = snapshot.get("plan_name", "").lower()
        is_silver_or_free = "silver" in plan_name or "free" in plan_name

        if task_type == "photoshoot_image":
            quality = params.get("image_quality", "2k")
            if quality == "2k":
                return 2
            elif quality == "4k":
                return 6 if "gold" in plan_name else 4

        elif task_type == "video_generation":
            resolution = params.get("resolution", "480p")
            if resolution == "480p":
                return 6 if is_silver_or_free else (5 if "gold" in plan_name else 4)
            elif resolution == "720p":
                return 8 if "gold" in plan_name else 5
            elif resolution == "1080p":
                return 6

        elif task_type == "model_create":
            return 10 if "gold" in plan_name else 7

        elif task_type in ["face_to_model", "model_swap"]:
            return 5 if "gold" in plan_name else 4
            
        elif task_type == "outerwear":
            return 6 if "gold" in plan_name else 4

        return 2  # Default fallback cost for Try-on / Smart Crop

    @staticmethod
    def deduct_resources(
        db: Session, 
        subscription: models.UserSubscription, 
        cost: int, 
        job_type: str,
        quota_key: Optional[models.ResourceKey] = None,
        reference_id: int = None
    ):
        """
        Expected Result: Safely deducts the credit cost and optionally increments a usage quota 
        (like image_to_video count). Commits the atomic transaction and logs the ledger entry.
        """
        # 1. Validate Credit Balance
        if subscription.credits_remaining < cost:
            logger.warning(f"Ledger Block: User {subscription.user_id} insufficient credits for {job_type}. Cost: {cost}, Balance: {subscription.credits_remaining}")
            raise APIException(
                status_code=200,
                msg=f"Insufficient credits. Required: {cost}, Available: {subscription.credits_remaining}"
            )

        # 2. Update Credit Balance
        subscription.credits_remaining -= cost
        
        # 3. Write to Immutable Ledger
        log_entry = models.UserResourceUsageLog(
            user_id=subscription.user_id,
            user_subscription_id=subscription.id,
            resource_key=models.ResourceKey.CREDITS,
            delta=-cost,
            used_after=subscription.credits_remaining,
            reference_type=job_type,
            reference_id=reference_id,
            description=f"Deduction for {job_type}"
        )
        db.add(log_entry)

        # 4. Increment Volume Quota (if applicable)
        if quota_key:
            usage_record = db.query(models.UserPlanResourceUsage).filter(
                models.UserPlanResourceUsage.user_subscription_id == subscription.id,
                models.UserPlanResourceUsage.resource_key == quota_key
            ).first()
            
            if usage_record:
                usage_record.used_value += 1

        # 5. Commit Atomic Transaction
        try:
            db.commit()
            db.refresh(subscription)
        except IntegrityError as e:
            db.rollback()
            logger.error(f"Ledger Integrity Error for User {subscription.user_id}: {str(e)}")
            raise APIException(status_code=500, msg="Transaction collision. Please try again.")

    @staticmethod
    def refund_resources(
        db: Session, 
        subscription: models.UserSubscription, 
        cost: int, 
        job_type: str,
        quota_key: Optional[models.ResourceKey] = None,
        reference_id: int = None,
        reason: str = "Job Failed"
    ):
        """
        Expected Result: If an AI engine (like Fashn.ai) fails, this restores the credits, 
        decrements the volume quota, and logs a positive refund entry to the ledger.
        """
        # 1. Restore Credit Balance
        subscription.credits_remaining += cost
        
        # 2. Write Refund to Immutable Ledger
        log_entry = models.UserResourceUsageLog(
            user_id=subscription.user_id,
            user_subscription_id=subscription.id,
            resource_key=models.ResourceKey.CREDITS,
            delta=cost,
            used_after=subscription.credits_remaining,
            reference_type=job_type,
            reference_id=reference_id,
            description=f"Refund for {job_type}: {reason}"
        )
        db.add(log_entry)

        # 3. Decrement Volume Quota (if applicable)
        if quota_key:
            usage_record = db.query(models.UserPlanResourceUsage).filter(
                models.UserPlanResourceUsage.user_subscription_id == subscription.id,
                models.UserPlanResourceUsage.resource_key == quota_key
            ).first()
            
            
            if usage_record and usage_record.used_value > 0:
                usage_record.used_value -= 1

        db.commit()