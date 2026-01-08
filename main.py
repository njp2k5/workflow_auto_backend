from fastapi import FastAPI
from api.routes import auth, health
from db.base import Base
from db.session import engine
from models.user import User 

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Multi Agent Insurance Claim Validation System")

#app.include_router(auth.router)
app.include_router(health.router)
app.include_router(auth.router)