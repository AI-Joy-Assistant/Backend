from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Optional
import jwt
from config.settings import settings
from .friends_service import FriendsService
from .friends_models import AddFriendRequest, MessageResponse

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
    
    if result["status"] == 200:
        return JSONResponse(
            status_code=result["status"],
            content={"message": result["message"]}
        )
    else:
        raise HTTPException(status_code=result["status"], detail=result["error"])

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

# 튜토리얼용 가이드 계정 정보
TUTORIAL_GUIDE = {
    "id": "tutorial_guide_joyner",
    "name": "조이너 가이드",
    "handle": "joyner_guide",
    "email": "guide@joyner.app"
}

@router.post("/tutorial/add-guide", summary="튜토리얼 가이드 친구 추가")
async def add_tutorial_guide(
    current_user_id: str = Depends(get_current_user_id)
):
    """
    튜토리얼용 가이드 계정을 친구로 자동 추가합니다.
    실제 DB에 가이드 계정이 없으면 가상의 친구 데이터를 반환합니다.
    """
    try:
        service = FriendsService()
        
        # 먼저 joyner_guide 계정이 실제로 존재하는지 확인
        guide_user = await service.repository.get_user_by_email_or_handle("joyner_guide")
        
        if guide_user:
            # 실제 계정이 존재하면 일반 친구 추가 프로세스
            result = await service.add_friend_by_email(current_user_id, "joyner_guide")
            
            if result["status"] == 200:
                # 자동으로 친구 요청 수락 (튜토리얼이므로)
                request_id = result["data"]["request_id"]
                # 가이드 계정 입장에서 수락 처리
                await service.repository.accept_friend_request_as_guide(
                    request_id, 
                    guide_user["id"]
                )
                
                return {
                    "success": True,
                    "message": "조이너 가이드와 친구가 되었습니다!",
                    "friend": {
                        "id": guide_user["id"],
                        "name": guide_user["name"],
                        "email": guide_user["email"],
                        "picture": guide_user.get("profile_image")
                    }
                }
            elif "이미 친구" in result.get("error", "") or "already" in result.get("error", "").lower():
                return {
                    "success": True,
                    "message": "이미 조이너 가이드와 친구입니다!",
                    "friend": {
                        "id": guide_user["id"],
                        "name": guide_user["name"],
                        "email": guide_user["email"],
                        "picture": guide_user.get("profile_image")
                    }
                }
            else:
                # 다른 에러면 가상 친구로 처리
                pass
        
        # 실제 계정이 없거나 추가 실패 시 가상의 친구 데이터 반환
        return {
            "success": True,
            "message": "조이너 가이드와 친구가 되었습니다! (튜토리얼 모드)",
            "is_virtual": True,
            "friend": {
                "id": TUTORIAL_GUIDE["id"],
                "name": TUTORIAL_GUIDE["name"],
                "email": TUTORIAL_GUIDE["email"],
                "picture": "https://api.dicebear.com/7.x/bottts/svg?seed=joyner_guide&backgroundColor=b6e3f4"
            }
        }
        
    except Exception as e:
        print(f"튜토리얼 가이드 추가 오류: {e}")
        # 에러 시에도 가상 친구로 처리 (튜토리얼 진행을 위해)
        return {
            "success": True,
            "message": "조이너 가이드와 친구가 되었습니다! (튜토리얼 모드)",
            "is_virtual": True,
            "friend": {
                "id": TUTORIAL_GUIDE["id"],
                "name": TUTORIAL_GUIDE["name"],
                "email": TUTORIAL_GUIDE["email"],
                "picture": "https://api.dicebear.com/7.x/bottts/svg?seed=joyner_guide&backgroundColor=b6e3f4"
            }
        }

@router.get("/tutorial/guide-info", summary="튜토리얼 가이드 정보 조회")
async def get_tutorial_guide_info():
    """튜토리얼 가이드 계정 정보를 반환합니다."""
    return {
        "id": TUTORIAL_GUIDE["id"],
        "name": TUTORIAL_GUIDE["name"],
        "handle": TUTORIAL_GUIDE["handle"],
        "email": TUTORIAL_GUIDE["email"],
        "picture": "https://api.dicebear.com/7.x/bottts/svg?seed=joyner_guide&backgroundColor=b6e3f4"
    }