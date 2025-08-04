from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Optional
import jwt
from config.settings import settings
from .service import FriendsService
from .models import AddFriendRequest, MessageResponse

router = APIRouter(prefix="/friends", tags=["Friends"])

def get_current_user_id(request: Request) -> str:
    """JWT 토큰에서 사용자 ID 추출"""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증 토큰이 필요합니다.")
    
    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("id")
        if not user_id:
            raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
        return str(user_id)
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")

@router.get("/requests", summary="친구 요청 목록 조회")
async def get_friend_requests(current_user_id: str = Depends(get_current_user_id)):
    """받은 친구 요청 목록을 조회합니다."""
    result = await FriendsService().get_friend_requests(current_user_id)
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.post("/requests/{request_id}/accept", summary="친구 요청 수락")
async def accept_friend_request(
    request_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """친구 요청을 수락합니다."""
    result = await FriendsService().accept_friend_request(request_id, current_user_id)
    
    return JSONResponse(
        status_code=result["status"],
        content={"message": result["message"]}
    )

@router.post("/requests/{request_id}/reject", summary="친구 요청 거절")
async def reject_friend_request(
    request_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """친구 요청을 거절합니다."""
    result = await FriendsService().reject_friend_request(request_id, current_user_id)
    
    return JSONResponse(
        status_code=result["status"],
        content={"message": result["message"]}
    )

@router.get("/list", summary="친구 목록 조회")
async def get_friends(current_user_id: str = Depends(get_current_user_id)):
    """친구 목록을 조회합니다."""
    result = await FriendsService().get_friends(current_user_id)
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.delete("/{friend_id}", summary="친구 삭제")
async def delete_friend(
    friend_id: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """친구를 삭제합니다."""
    result = await FriendsService().delete_friend(current_user_id, friend_id)
    
    return JSONResponse(
        status_code=result["status"],
        content={"message": result["message"]}
    )

@router.post("/add", summary="이메일로 친구 추가")
async def add_friend_by_email(
    request: AddFriendRequest,
    current_user_id: str = Depends(get_current_user_id)
):
    """이메일로 친구를 추가합니다."""
    result = await FriendsService().add_friend_by_email(current_user_id, request.email)
    
    if result["status"] == 200:
        return {
            "message": result["message"],
            "data": result["data"]
        }
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.get("/search", summary="사용자 검색")
async def search_users(
    query: str,
    current_user_id: str = Depends(get_current_user_id)
):
    """사용자를 검색합니다."""
    result = await FriendsService().search_users(query, current_user_id)
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

# 테스트용 엔드포인트 (인증 없음)
@router.get("/test/friends", summary="친구 목록 테스트 (인증 없음)")
async def test_friends():
    """인증 없이 친구 목록을 테스트합니다."""
    # 고정 사용자 ID로 테스트
    result = await FriendsService().get_friends("test-user-id")
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

@router.get("/test/requests", summary="친구 요청 목록 테스트 (인증 없음)")
async def test_friend_requests():
    """인증 없이 친구 요청 목록을 테스트합니다."""
    # 고정 사용자 ID로 테스트
    result = await FriendsService().get_friend_requests("test-user-id")
    
    if result["status"] == 200:
        return result["data"]
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"]) 