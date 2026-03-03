from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from config.settings import settings

class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        
        user_id = event.from_user.id
        allowed_users = settings.TELEGRAM_ALLOWED_USERS
        
        if user_id in allowed_users or user_id == settings.TELEGRAM_ADMIN_USER:
            return await handler(event, data)
        
        await event.answer(f"Access denied. Your ID: {user_id}")
        return
