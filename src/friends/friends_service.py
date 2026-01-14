from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
from .friends_repository import FriendsRepository
from .friends_models import AddFriendRequest
from src.websocket.websocket_manager import manager as ws_manager
from src.chat.chat_repository import ChatRepository

KST = timezone(timedelta(hours=9))

class FriendsService:
    def __init__(self):
        self.repository = FriendsRepository()
    
    async def add_friend_by_email(self, current_user_id: str, email: str) -> Dict[str, Any]:
        """이메일 또는 handle로 친구 추가"""
        try:
            print(f"📧 친구 추가 요청: user_id={current_user_id}, identifier={email}")
            
            # 이메일 또는 handle로 사용자 찾기
            user = await self.repository.get_user_by_email_or_handle(email)
            print(f"🔍 사용자 조회 결과: {user}")
            
            if not user:
                print(f"❌ 사용자를 찾을 수 없음: {email}")
                return {
                    "status": 404,
                    "error": "해당 이메일 또는 아이디의 사용자를 찾을 수 없습니다."
                }
            
            if user['id'] == current_user_id:
                print(f"❌ 자기 자신을 친구로 추가하려고 시도")
                return {
                    "status": 400,
                    "error": "자기 자신을 친구로 추가할 수 없습니다."
                }
            
            # 친구 요청 생성
            print(f"✉️ 친구 요청 생성 시도: from={current_user_id}, to={user['id']}")
            result = await self.repository.create_friend_request(current_user_id, user['id'])
            
            if result["success"]:
                print(f"✅ 친구 요청 생성 성공")
                
                # WebSocket으로 상대방에게 실시간 알림 전송
                try:
                    # 요청자 이름 조회
                    from_user = await self.repository.get_user_by_id(current_user_id)
                    from_name = from_user.get('name', '사용자') if from_user else '사용자'
                    
                    await ws_manager.send_personal_message({
                        "type": "friend_request",
                        "request_id": result["data"]["id"],
                        "from_user_id": current_user_id,
                        "from_user_name": from_name,
                        "timestamp": datetime.now(KST).isoformat()
                    }, user['id'])
                    print(f"[WS] 친구 요청 알림 전송: {user['id']}")
                except Exception as ws_err:
                    print(f"[WS] 친구 요청 알림 전송 실패: {ws_err}")
                
                return {
                    "status": 200,
                    "message": "친구 요청을 보냈습니다.",
                    "data": {
                        "request_id": result["data"]["id"],
                        "to_user": {
                            "id": user['id'],
                            "name": user['name'],
                            "email": user['email'],
                            "picture": user.get('picture')
                        }
                    }
                }
            else:
                print(f"❌ 친구 요청 생성 실패: {result['message']}")
                return {
                    "status": 400,
                    "error": result["message"]
                }
        except Exception as e:
            print(f"친구 추가 오류: {e}")
            return {
                "status": 500,
                "error": "친구 추가 중 오류가 발생했습니다."
            }
    
    async def get_friend_requests(self, user_id: str) -> Dict[str, Any]:
        """친구 요청 목록 조회"""
        try:
            requests = await self.repository.get_friend_requests(user_id)
            
            formatted_requests = []
            for request in requests:
                request_user = request.get('request_user', {})
                formatted_requests.append({
                    "id": request['id'],
                    "from_user": {
                        "id": request_user.get('id'),
                        "name": request_user.get('name'),
                        "email": request_user.get('email'),
                        "picture": request_user.get('profile_image')
                    },
                    "status": request['follow_status'],
                    "created_at": request['requested_at']
                })
            
            return {
                "status": 200,
                "data": {
                    "requests": formatted_requests,
                    "total_count": len(formatted_requests)
                }
            }
        except Exception as e:
            print(f"친구 요청 목록 조회 오류: {e}")
            return {
                "status": 500,
                "error": "친구 요청 목록 조회 중 오류가 발생했습니다."
            }
    
    async def accept_friend_request(self, request_id: str, user_id: str) -> Dict[str, Any]:
        """친구 요청 수락"""
        try:
            result = await self.repository.accept_friend_request(request_id, user_id)
            
            if result["success"]:
                # WebSocket으로 요청자에게 수락 알림 전송
                try:
                    if result.get("from_user_id"):
                        await ws_manager.send_personal_message({
                            "type": "friend_accepted",
                            "request_id": request_id,
                            "accepted_by": user_id,
                            "timestamp": datetime.now(KST).isoformat()
                        }, result["from_user_id"])
                        print(f"[WS] 친구 수락 알림 전송: {result['from_user_id']}")
                except Exception as ws_err:
                    print(f"[WS] 친구 수락 알림 전송 실패: {ws_err}")
                
                # [Notification] 로그 기록 (History)
                try:
                    self.repository.supabase.table("chat_log").insert({
                        "user_id": result["from_user_id"],
                        "friend_id": user_id,
                        "request_text": None,
                        "response_text": f"친구 요청이 수락되었습니다.",
                        "message_type": "friend_accepted",
                        "created_at": datetime.now(KST).isoformat()
                    }).execute()
                except Exception as log_err:
                    print(f"친구 수락 로그 기록 실패: {log_err}")
                
                return {
                    "status": 200,
                    "message": result["message"]
                }
            else:
                return {
                    "status": 400,
                    "error": result["message"]
                }
        except Exception as e:
            print(f"친구 요청 수락 오류: {e}")
            return {
                "status": 500,
                "error": "친구 요청 수락 중 오류가 발생했습니다."
            }
    
    async def reject_friend_request(self, request_id: str, user_id: str) -> Dict[str, Any]:
        """친구 요청 거절"""
        try:
            result = await self.repository.reject_friend_request(request_id, user_id)
            
            if result["success"]:
                # 1. WebSocket 알림 (거절)
                try:
                    if result.get("from_user_id"):
                        await ws_manager.send_personal_message({
                            "type": "friend_rejected",
                            "request_id": request_id,
                            "rejected_by": user_id,
                            "timestamp": datetime.now(KST).isoformat()
                        }, result["from_user_id"])
                        
                        # 2. 로그 기록 (History)
                        self.repository.supabase.table("chat_log").insert({
                            "user_id": result["from_user_id"],
                            "friend_id": user_id,
                            "request_text": None,
                            "response_text": f"친구 요청이 거절되었습니다.",
                            "message_type": "friend_rejected",
                            "created_at": datetime.now(KST).isoformat()
                        }).execute()
                except Exception as e:
                    print(f"친구 거절 알림/로그 처리 실패: {e}")

                return {
                    "status": 200,
                    "message": result["message"]
                }
            else:
                return {
                    "status": 400,
                    "error": result["message"]
                }
        except Exception as e:
            print(f"친구 요청 거절 오류: {e}")
            return {
                "status": 500,
                "error": "친구 요청 거절 중 오류가 발생했습니다."
            }
    
    async def get_friends(self, user_id: str) -> Dict[str, Any]:
        """친구 목록 조회"""
        try:
            friends = await self.repository.get_friends(user_id)
            
            formatted_friends = []
            for friend in friends:
                friend_user = friend.get('friend_user', {})
                formatted_friends.append({
                    "id": friend['id'],
                    "friend": {
                        "id": friend_user.get('id'),
                        "name": friend_user.get('name'),
                        "email": friend_user.get('email'),
                        "picture": friend_user.get('profile_image')
                    },
                    "created_at": friend['created_at']
                })
            
            return {
                "status": 200,
                "data": {
                    "friends": formatted_friends,
                    "total_count": len(formatted_friends)
                }
            }
        except Exception as e:
            print(f"친구 목록 조회 오류: {e}")
            return {
                "status": 500,
                "error": "친구 목록 조회 중 오류가 발생했습니다."
            }
    
    async def delete_friend(self, user_id: str, friend_id: str) -> Dict[str, Any]:
        """친구 삭제"""
        try:
            result = await self.repository.delete_friend(user_id, friend_id)
            
            if result["success"]:
                return {
                    "status": 200,
                    "message": result["message"]
                }
            else:
                return {
                    "status": 400,
                    "error": result["message"]
                }
        except Exception as e:
            print(f"친구 삭제 오류: {e}")
            return {
                "status": 500,
                "error": "친구 삭제 중 오류가 발생했습니다."
            }
    
    async def search_users(self, query: str, current_user_id: str) -> Dict[str, Any]:
        """사용자 검색"""
        try:
            if len(query.strip()) < 2:
                return {
                    "status": 400,
                    "error": "검색어는 2글자 이상 입력해주세요."
                }
            
            users = await self.repository.search_users(query, current_user_id)
            
            formatted_users = []
            for user in users:
                formatted_users.append({
                    "id": user['id'],
                    "name": user['name'],
                    "email": user['email'],
                    "picture": user.get('profile_image')
                })
            
            return {
                "status": 200,
                "data": {
                    "users": formatted_users,
                    "total_count": len(formatted_users)
                }
            }
        except Exception as e:
            print(f"사용자 검색 오류: {e}")
            return {
                "status": 500,
                "error": "사용자 검색 중 오류가 발생했습니다."
            } 