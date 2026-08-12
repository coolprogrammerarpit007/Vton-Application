from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from . import models, schemas
from .database import get_db
from .exceptions import APIException

router = APIRouter(prefix="/api/pages", tags=["Public Pages"])

@router.get("/{slug}", response_model=schemas.StandardResponse)
async def get_page_content(slug: str, db: Session = Depends(get_db)):
    """
    Fetches the HTML content for public pages dynamically based on the slug.
    Examples: /api/pages/privacy-policy OR /api/pages/terms-conditions
    """
    page = db.query(models.Page).filter(
        models.Page.slug == slug,
        models.Page.deleted_at.is_(None) # Ignores soft-deleted pages
    ).first()

    if not page:
        raise APIException(status_code=200, msg="Page not found.")

    return schemas.StandardResponse(
        status=True,
        msg=f"{page.title} retrieved successfully.",
        data={
            "id": page.id,
            "title": page.title,
            "slug": page.slug,
            "content": page.content  # Returns the raw HTML string
        }
    )