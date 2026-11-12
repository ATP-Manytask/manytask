from pydantic import BaseModel


class AppMountConfig(BaseModel):
    cache: str
    solutions: str