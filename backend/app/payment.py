import hashlib
import time
import random
import logging
from typing import Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from . import models, schemas
from .database import get_db
from .config import settings
from .exceptions import APIException
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/payment", tags=["Payment Integration"])

def generate_request_hash(data: dict) -> str:
    """
    Calculates SHA-512 request hash for PayU:
    sha512(key|txnid|amount|productinfo|firstname|email|udf1|udf2|udf3|udf4|udf5||||||SALT)
    """
    hash_sequence = (
        f"{settings.PAYU_MERCHANT_KEY}|{data.get('txnid', '')}|{data.get('amount', '')}|"
        f"{data.get('productinfo', '')}|{data.get('firstname', '')}|{data.get('email', '')}|"
        f"{data.get('udf1', '')}|{data.get('udf2', '')}|{data.get('udf3', '')}|"
        f"{data.get('udf4', '')}|{data.get('udf5', '')}||||||{settings.PAYU_MERCHANT_SALT}"
    )
    return hashlib.sha512(hash_sequence.encode('utf-8')).hexdigest().lower()

def validate_response_hash(data: dict) -> bool:
    """
    Calculates reverse SHA-512 response hash from PayU:
    sha512(SALT|status||||||udf5|udf4|udf3|udf2|udf1|email|firstname|productinfo|amount|txnid|key)
    """
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


@router.post("/initiate", response_model=schemas.StandardPaymentResponse)
async def initiate_payment(
    req: schemas.PaymentInitiateRequest,
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(get_current_user)
):
    """
    Initiates payment request, records transaction state in DB, and yields PayU payload.
    """
    txnid = f"VTON{int(time.time())}{random.randint(1000, 9999)}"
    user_id = current_user.id if current_user else None

    # 1. Create DB record in PENDING status
    transaction = models.PaymentTransaction(
        txnid=txnid,
        user_id=user_id,
        amount=req.amount,
        firstname=req.firstname,
        email=req.email,
        phone=req.phone,
        product_info=req.productinfo,
        status=models.TransactionStatus.PENDING
    )
    db.add(transaction)
    db.commit()

    # 2. Build PayU Payload
    payment_data = {
        "key": settings.PAYU_MERCHANT_KEY,
        "txnid": txnid,
        "amount": req.amount,
        "productinfo": req.productinfo,
        "firstname": req.firstname,
        "email": req.email,
        "phone": req.phone,
        "surl": f"{settings.BACKEND_URL}/api/payment/callback?type=success", 
        "furl": f"{settings.BACKEND_URL}/api/payment/callback?type=fail",
        "udf1": transaction.id,
        "udf2": "",
        "udf3": "",
        "udf4": "",
        "udf5": ""
    }
    
    payment_data["hash"] = generate_request_hash(payment_data)

    return schemas.StandardPaymentResponse(
        status=True,
        msg="Payment transaction initiated successfully.",
        data={
            "action_url": settings.PAYU_BASE_URL,
            "payment_data": payment_data
        }
    )


@router.post("/callback")
async def payment_callback(request: Request, db: Session = Depends(get_db)):
    """
    Web-hook / Return callback posted by PayU upon completing transaction processing.
    """
    form_data = await request.form()
    data_dict = dict(form_data)
    
    txnid = data_dict.get('txnid', '')
    status = data_dict.get('status', '')
    payu_money_id = data_dict.get('mihpayid', '')

    logger.info(f"PayU Callback received for TxnID: {txnid} | Status: {status}")

    # 1. Locate Transaction Record
    txn = db.query(models.PaymentTransaction).filter(models.PaymentTransaction.txnid == txnid).first()
    if not txn:
        logger.error(f"Callback error: Transaction ID {txnid} not found in DB.")
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/payment-status?status=failed&reason=invalid_txnid",
            status_code=303
        )

    # 2. Validate Response Hash
    is_valid = validate_response_hash(data_dict)
    txn.raw_response = data_dict
    txn.payu_money_id = payu_money_id

    if not is_valid:
        logger.warning(f"SECURITY ALERT: Hash mismatch for TxnID: {txnid}")
        txn.status = models.TransactionStatus.TAMPERED
        db.commit()
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/payment-status?status=failed&reason=hash_mismatch&txnid={txnid}",
            status_code=303
        )

    # 3. Handle Status Update
    if status == 'success':
        txn.status = models.TransactionStatus.SUCCESS
        
        # Optional: Grant plan or credits to user here
        if txn.user_id:
            user = db.query(models.User).filter(models.User.id == txn.user_id).first()
            if user:
                user.plan_name = "PRO" # Update user subscription tier
                
        db.commit()
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/payment-status?status=success&txnid={txnid}",
            status_code=303
        )
    else:
        txn.status = models.TransactionStatus.FAILED
        db.commit()
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/payment-status?status=failed&txnid={txnid}",
            status_code=303
        )

