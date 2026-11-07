from fastapi import APIRouter, Depends, Query

from app.api.deps import get_client
from app.core.security import ClientInfo
from app.services import container

router = APIRouter(prefix="/usage", tags=["Usage"])


@router.get("/me")
async def my_usage(
    since_hours: float | None = Query(default=None, gt=0, le=24 * 365),
    client: ClientInfo = Depends(get_client),
):
    return await container.usage_repo.summary(client.name, since_hours)
