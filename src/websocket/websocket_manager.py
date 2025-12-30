"""
WebSocket Manager for real-time notifications
"""
from fastapi import WebSocket
from typing import Dict, List
import logging
import json

logger = logging.getLogger(__name__)


class ConnectionManager:
    """WebSocket 연결 관리자"""
    
    def __init__(self):
        # user_id -> list of WebSocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, user_id: str):
        """새 WebSocket 연결 수락"""
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
        logger.info(f"[WS] 사용자 {user_id} 연결됨 (총 {len(self.active_connections[user_id])}개 연결)")
    
    def disconnect(self, websocket: WebSocket, user_id: str):
        """WebSocket 연결 해제"""
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        logger.info(f"[WS] 사용자 {user_id} 연결 해제됨")
    
    async def send_personal_message(self, message: dict, user_id: str):
        """특정 사용자에게 메시지 전송"""
        logger.info(f"[WS] 메시지 전송 시도 - user_id: {user_id}, 연결상태: {self.is_user_connected(user_id)}")
        logger.info(f"[WS] 현재 연결된 사용자들: {list(self.active_connections.keys())}")
        
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_json(message)
                    logger.info(f"[WS] 메시지 전송 성공: {user_id}")
                except Exception as e:
                    logger.warning(f"[WS] 메시지 전송 실패: {e}")
    
    async def broadcast_to_users(self, message: dict, user_ids: List[str]):
        """여러 사용자에게 메시지 전송"""
        for user_id in user_ids:
            await self.send_personal_message(message, user_id)
    
    def is_user_connected(self, user_id: str) -> bool:
        """사용자가 연결되어 있는지 확인"""
        return user_id in self.active_connections and len(self.active_connections[user_id]) > 0


# 전역 WebSocket 매니저 인스턴스
manager = ConnectionManager()
