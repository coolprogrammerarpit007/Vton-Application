import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

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


@router.get("/{plan_name}", response_model=schemas.StandardResponse)
async def get_subscription_plan_by_name(plan_name: str, db: Session = Depends(get_db)):
    """
    Fetches details for a single plan (e.g. /api/plans/gold).
    """
    plan = db.query(models.SubscriptionPlan).filter(
        models.SubscriptionPlan.plan_name == plan_name.lower(),
        models.SubscriptionPlan.is_active == True
    ).first()

    if not plan:
        raise APIException(status_code=404, msg="Subscription plan not found.")

    plan_dict = {
        "id": plan.id,
        "plan_name": plan.plan_name,
        "title": plan.title,
        "price": plan.price,
        "credits": plan.credits,
        "is_active": plan.is_active
    }

    return schemas.StandardResponse(
        status=True,
        msg="Subscription plan details retrieved successfully.",
        data=plan_dict
    )