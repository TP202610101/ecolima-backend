from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.endpoints import auth, users, datasets, map as map_, ml, geo

app = FastAPI(title="EcoLima Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(users.router, prefix="/api/v1/admin/users", tags=["users"])
app.include_router(datasets.router, prefix="/api/v1/datasets", tags=["datasets"])
app.include_router(map_.router, prefix="/api/v1/map", tags=["map"])
app.include_router(ml.router, prefix="/api/v1/ml", tags=["ml"])
app.include_router(geo.router, prefix="/api/v1/geo", tags=["geo"])
