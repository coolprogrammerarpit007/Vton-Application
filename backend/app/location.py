import httpx
import logging
from fastapi import APIRouter, Path
from . import schemas
from .exceptions import APIException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/location", tags=["Location & Postal Services"])

POSTAL_API_URL = "https://api.postalpincode.in/pincode"

@router.get("/pincode/{pincode}", response_model=schemas.StandardPincodeResponse)
async def lookup_pincode_details(
    pincode: str = Path(..., min_length=6, max_length=6, regex="^[1-9][0-9]{5}$", description="Valid 6-digit Indian PIN Code")
):
    """
    Fetches District, State, City, and all associated Post Offices for a given Indian 6-digit PIN code.
    """
    clean_pincode = pincode.strip()
    target_url = f"{POSTAL_API_URL}/{clean_pincode}"
    
    logger.info(f"Looking up location details for PIN Code: {clean_pincode}")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await client.get(target_url)
            
            if response.status_code != 200:
                logger.error(f"Postal API upstream returned status code {response.status_code}")
                raise APIException(status_code=200, msg="Unable to retrieve PIN code details from postal service.")
            
            data = response.json()
            
            # The API returns a list with status objects
            if not isinstance(data, list) or len(data) == 0:
                raise APIException(status_code=200, msg="No records found for the provided PIN code.")
            
            result_block = data[0]
            api_status = result_block.get("Status")
            
            if api_status != "Success" or not result_block.get("PostOffice"):
                error_msg = result_block.get("Message", "Invalid PIN code or no location records found.")
                raise APIException(status_code=200, msg=error_msg)
            
            raw_post_offices = result_block.get("PostOffice") or []
            
            # Extract common administrative details from the first matching branch
            first_po = raw_post_offices[0]
            district = first_po.get("District") or ""
            state = first_po.get("State") or ""
            country = first_po.get("Country") or "India"
            city = first_po.get("Division") or district

            # Clean and map the list of post offices
            post_offices_list = []
            for po in raw_post_offices:
                post_offices_list.append(
                    schemas.PostOfficeDetail(
                        name=po.get("Name") or "",
                        branch_type=po.get("BranchType") or "",
                        delivery_status=po.get("DeliveryStatus") or "",
                        district=po.get("District") or district,
                        state=po.get("State") or state,
                        country=po.get("Country") or country,
                        pincode=po.get("Pincode") or clean_pincode
                    )
                )

            return schemas.StandardPincodeResponse(
                status=True,
                msg=f"Found {len(post_offices_list)} post office location(s) for PIN code {clean_pincode}.",
                data=schemas.PincodeLookupData(
                    pincode=clean_pincode,
                    city=city,
                    district=district,
                    state=state,
                    country=country,
                    post_offices=post_offices_list
                )
            )

    except APIException:
        raise
    except httpx.RequestError as exc:
        logger.error(f"Network error calling Postal PIN Code API: {str(exc)}")
        raise APIException(status_code=200, msg="Postal service network timeout. Please try again.")
    except Exception as exc:
        logger.error(f"Unexpected error processing PIN Code lookup: {str(exc)}", exc_info=True)
        raise APIException(status_code=200, msg="Failed to process PIN code lookup.")
