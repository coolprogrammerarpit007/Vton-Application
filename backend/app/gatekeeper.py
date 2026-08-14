import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from . import models
from .database import get_db
from .auth import get_current_user
from .exceptions import APIException

logger = logging.getLogger(__name__)


def extract_enum_or_val(obj, attr_name: str, default=None):
    val = getattr(obj, attr_name, default)
    if hasattr(val, "value"):
        return val.value
    return val


def auto_provision_free_tier(db: Session, user_id: int) -> models.UserSubscription:
    """
    Provisions a Free Tier Subscription (3 credits, Silver features) 
    in the database for first-time users upon their first feature usage.
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
    db.flush() # Flush to generate the free_sub.id needed for usage tables
    
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

    # 4. Write Assignment Log to History
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

        snapshot = subscription.plan_snapshot or {}

        # 2. Check Boolean Feature Flag Access
        if self.feature_flag:
            has_access = snapshot.get(self.feature_flag, False)
            if not has_access:
                feature_name_map = {
                    "create_model_enabled": "AI Model Creation",
                    "model_swap": "Model Swap",
                    "face_to_model": "Face to Model",
                    "product_to_model": "Product to Model",
                    "change_background": "Background Replacement",
                    "outerwear_enabled": "Outerwear Try-On"
                }
                friendly_name = feature_name_map.get(self.feature_flag, self.feature_flag.replace("_", " ").title())
                
                logger.warning(f"Gatekeeper Block: User {current_user.id} attempted restricted feature '{friendly_name}'.")
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

        # 4. Validate Video Export Quality
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

        # 5. Check Resource Usage Limits (Volume Caps)
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
    Utility class to handle atomic deduction and refund of credits and volume quotas.
    Now fully database-driven for dynamic admin control.
    """
    
    
    @staticmethod
    def get_actual_fashn_cost(job_type: str, params: Optional[Dict[str, Any]] = None) -> float:
        """
        Maps feature keys to EXACT Fashn.ai wholesale compute costs.
        Gracefully handles missing params with default production values.
        """
        job_key = (job_type or "").lower().strip()
        params = params or {}

        # 1. Image-to-Video Engine (Dynamic based on duration & resolution)
        if job_key in ["image_to_video", "video_generation"]:
            duration = int(params.get("duration", 5))
            resolution = str(params.get("resolution", "1080p")).lower()

            if duration >= 10:
                video_cost_map = {"480p": 2.0, "720p": 6.0, "1080p": 12.0}
            else:
                video_cost_map = {"480p": 1.0, "720p": 3.0, "1080p": 6.0}

            return video_cost_map.get(resolution, 6.0)

        # 2. Local-only utilities (Cost $0 compute at Fashn)
        if job_key == "smart_crop":
            return 0.00

        # 3. Static Wholesale Mapping for Image Endpoints (1k / Balanced defaults)
        wholesale_pricing = {
            "tryon": 2.0,
            "vton": 2.0,
            "three_sixty": 4.0,
            "outerwear": 2.0,
            "outfit": 4.0,
            "product_to_model": 2.0,
            "model_create": 1.0,
            "model_swap": 1.0,
            "face_to_model": 1.0,
            "change_background": 1.0,
            "background_remove": 1.0,
        }

        base_cost = wholesale_pricing.get(job_key, 1.0)

        if params.get("face_reference"):
            base_cost += 3.0

        num_images = int(params.get("num_images", 1))
        return float(base_cost * max(1, num_images))

    @staticmethod
    def _get_current_master_balances(db: Session) -> tuple[float, float]:
        """Returns (current_fashn_balance, current_virtual_balance)"""
        latest_entry = (
            db.query(models.MpxFashnApiPayment)
            .order_by(models.MpxFashnApiPayment.id.desc())
            .first()
        )
        if latest_entry:
            fashn_bal = float(latest_entry.fashn_balance_after) if latest_entry.fashn_balance_after is not None else 0.0
            virt_bal = float(latest_entry.virtual_balance_after) if latest_entry.virtual_balance_after is not None else 0.0
            return fashn_bal, virt_bal

        # Fallback if table is completely empty
        return 0.0, 0.0
    
    @staticmethod
    def calculate_cost(
        db: Session, 
        subscription_plan_id: int, 
        action_key: str, 
        params: Dict[str, Any] = {}
    ) -> int:
        """
        Dynamically calculates the total credit cost of a job by combining the base cost 
        from the database with dynamic request multipliers (images, duration).
        """
        # 1. Advanced Key Resolution for Overrides
        # This allows an admin to create specific keys like "image_to_video_1080p" 
        # or "photoshoot_image_4k" to charge premium rates for high-res outputs.
        resolution = params.get("resolution", "").lower()
        search_keys = [f"{action_key}_{resolution}", action_key] if resolution else [action_key]
        
        cost_record = None
        for key in search_keys:
            cost_record = db.query(models.PlanActionCost).filter(
                models.PlanActionCost.subscription_plan_id == subscription_plan_id,
                models.PlanActionCost.action_key == key,
                models.PlanActionCost.is_active == True
            ).first()
            
            if cost_record:
                break
                
        # Fallback safeguard in case the admin hasn't configured a specific feature key yet
        base_cost = cost_record.credits if cost_record else 2 

        # 2. Apply Multipliers for Quantity
        # E.g., if base_cost is 2, and user requests 3 images, total = 6
        try:
            num_images = int(params.get("num_images", 1))
        except (ValueError, TypeError):
            num_images = 1
            
        # 3. Apply Multipliers for Video Duration 
        # Assuming base cost covers a standard 5-second video. 
        # A 10-second video automatically doubles the base cost.
        try:
            duration = int(params.get("duration", 5))
            duration_multiplier = max(1, duration // 5)
        except (ValueError, TypeError):
            duration_multiplier = 1

        # 4. Final Mathematical Calculation
        total_cost = base_cost * num_images * duration_multiplier
        
        return total_cost

    @staticmethod
    def deduct_resources(
        db: Session, 
        subscription: models.UserSubscription, 
        cost: int, 
        job_type: str,
        quota_key: Optional[models.ResourceKey] = None,
        reference_id: int = None,
        params: Optional[Dict[str, Any]] = None
        
    ):
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
        
        
        exact_fashn_cost = SubscriptionTransactionManager.get_actual_fashn_cost(job_type, params=params)
        current_fashn_bal, current_virt_bal = SubscriptionTransactionManager._get_current_master_balances(db)
        
        new_fashn_balance = round(current_fashn_bal - exact_fashn_cost, 2)
        new_virtual_balance = round(current_virt_bal - float(cost), 2) # <-- Deducting the virtual cost
        
        master_debit = models.MpxFashnApiPayment(
            user_id=subscription.user_id,
            api_type="WALLET AMOUNT",
            fashn_amount=exact_fashn_cost,
            amount=float(cost),
            comment=f"Debit for {job_type} (Job Ref: {reference_id})",
            amount_type="dr",
            user_balance_after=subscription.credits_remaining,
            fashn_balance_after=new_fashn_balance,
            virtual_balance_after=new_virtual_balance # <-- Saving the snapshot
        )
        db.add(master_debit)
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
            raise APIException(status_code=200, msg="Transaction collision. Please try again.")

    @staticmethod
    def refund_resources(
        db: Session, 
        subscription: models.UserSubscription, 
        cost: int, 
        job_type: str,
        quota_key: Optional[models.ResourceKey] = None,
        reference_id: int = None,
        reason: str = "Job Failed",
        params: Optional[Dict[str, Any]] = None
    ):
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
        
        
        exact_fashn_cost = SubscriptionTransactionManager.get_actual_fashn_cost(job_type, params=params)
        current_fashn_bal, current_virt_bal = SubscriptionTransactionManager._get_current_master_balances(db)
        
        new_fashn_balance = round(current_fashn_bal + exact_fashn_cost, 2)
        new_virtual_balance = round(current_virt_bal + float(cost), 2) # <-- Refunding the virtual cost
        
        master_credit = models.MpxFashnApiPayment(
            user_id=subscription.user_id,
            api_type="WALLET AMOUNT",
            fashn_amount=exact_fashn_cost,
            amount=float(cost),
            comment=f"Refund for failed {job_type} (Job Ref: {reference_id})",
            amount_type="cr",
            user_balance_after=subscription.credits_remaining,
            fashn_balance_after=new_fashn_balance,
            virtual_balance_after=new_virtual_balance # <-- Saving the snapshot
        )
        db.add(master_credit)

        # 4. Decrement Volume Quota (if applicable)
        if quota_key:
            usage_record = db.query(models.UserPlanResourceUsage).filter(
                models.UserPlanResourceUsage.user_subscription_id == subscription.id,
                models.UserPlanResourceUsage.resource_key == quota_key
            ).first()
            
            if usage_record and usage_record.used_value > 0:
                usage_record.used_value -= 1

        db.commit()