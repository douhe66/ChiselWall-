"""API 包（设计 §11）：FastAPI 本地接口，127.0.0.1 + token 鉴权（评审结论③ P0）。"""
from .server import create_app, load_or_create_token

__all__ = ["create_app", "load_or_create_token"]
