from pydantic import BaseModel

class UserCreate(BaseModel):
    username: str
    designation: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str
    member_name: str
    designation: str
