from fastapi import APIRouter

router = APIRouter()

@router.get("/recommendations")
async def ml_recommendations():
    return {"message": "ml endpoint placeholder"}
