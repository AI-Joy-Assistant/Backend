#!/usr/bin/env python3
"""
Supabase 연결 테스트 스크립트
"""

import asyncio
import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.database import get_supabase_client
from config.settings import settings

async def test_supabase_connection():
    """Supabase 연결 테스트"""
    try:
        print("🔍 Supabase 연결 테스트 시작...")
        print(f"URL: {settings.SUPABASE_URL}")
        print(f"Key set: {bool(settings.SUPABASE_SERVICE_KEY)}")
        
        supabase = get_supabase_client()
        
        # user 테이블 존재 확인
        print("\n📋 user 테이블 확인...")
        try:
            response = supabase.table('user').select('*').limit(1).execute()
            print(f"✅ user 테이블 접근 성공: {len(response.data)}개 레코드")
            
            # 테이블 구조 확인
            if response.data:
                print("📊 테이블 구조:")
                for key in response.data[0].keys():
                    print(f"  - {key}")
                
                # email 컬럼 존재 확인
                if 'email' in response.data[0]:
                    print("✅ email 컬럼 존재")
                else:
                    print("❌ email 컬럼 없음")
            else:
                print("📊 테이블이 비어있습니다.")
                
        except Exception as e:
            print(f"❌ user 테이블 접근 실패: {str(e)}")
        
        # 테이블 목록 확인
        print("\n📋 모든 테이블 확인...")
        try:
            # Supabase는 테이블 목록을 직접 조회할 수 없으므로, 
            # 주요 테이블들을 하나씩 확인
            tables = ['user', 'a2a', 'friend_list', 'chat_log', 'friend_follow']
            for table in tables:
                try:
                    response = supabase.table(table).select('*').limit(1).execute()
                    print(f"✅ {table} 테이블: 접근 가능")
                except Exception as e:
                    print(f"❌ {table} 테이블: 접근 불가 - {str(e)}")
        except Exception as e:
            print(f"❌ 테이블 목록 확인 실패: {str(e)}")
            
    except Exception as e:
        print(f"❌ Supabase 연결 실패: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_supabase_connection()) 