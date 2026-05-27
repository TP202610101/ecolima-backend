from fastapi import APIRouter

router = APIRouter()

@router.get("/points")
async def map_points():
    return {"message": "map points endpoint placeholder"}
