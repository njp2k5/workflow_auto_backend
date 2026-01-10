from fastapi import FastAPI
print("imported contents for main")
from api.routes import auth, health
print("imported routes")
from db.base import Base
print("imported base")
from db.session import engine
print("imported engine")
from models.user import User
print("imported user model")



def create_app() -> FastAPI:
    app = FastAPI(title="Multi Agent Insurance Claim Validation System")

    @app.on_event("startup")
    def startup() -> None:
        try:
            Base.metadata.create_all(bind=engine)
            print("Database tables created successfully.")
        except Exception as e:
            print(f"Error during startup: {e}")

    app.include_router(health.router)
    app.include_router(auth.router)

    return app


app = create_app()