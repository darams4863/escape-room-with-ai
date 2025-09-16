"""
사용자 예측 API 엔드포인트
ML 기반 개인화 예측 및 트렌드 분석
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from typing import Dict, List
import logging
from datetime import datetime, timedelta

from ..core.connections import postgres_manager
from ..ml.inference.prediction_service import PredictionService
from ..ml.data.feature_engineering import UserFeatureEngineer
from ..api.auth import get_current_user
from ..models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predictions", tags=["User Predictions"])

# 전역 예측 서비스 인스턴스
prediction_service = PredictionService()

@router.get("/health")
async def health_check():
    """ML 서비스 상태 확인"""
    return prediction_service.health_check()

@router.get("/user/{user_id}/trend")
async def get_user_trend(
    user_id: int,
    days_ahead: int = 7,
    current_user: User = Depends(get_current_user)
):
    """사용자 트렌드 예측"""
    try:
        # 사용자 데이터 조회
        user_data = await get_user_historical_data(user_id, days=30)
        
        if not user_data:
            raise HTTPException(status_code=404, detail="사용자 데이터를 찾을 수 없습니다")
        
        # 트렌드 예측
        trend_prediction = prediction_service.predict_user_trend(user_data, days_ahead)
        
        return {
            "user_id": user_id,
            "prediction": trend_prediction,
            "data_points": len(user_data),
            "requested_by": current_user.id
        }
        
    except Exception as e:
        logger.error(f"사용자 {user_id} 트렌드 예측 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/user/{user_id}/activity")
async def get_user_activity_prediction(
    user_id: int,
    current_user: User = Depends(get_current_user)
):
    """사용자 활동 예측"""
    try:
        # 사용자 데이터 조회
        user_data = await get_user_historical_data(user_id, days=30)
        
        if not user_data:
            raise HTTPException(status_code=404, detail="사용자 데이터를 찾을 수 없습니다")
        
        # 활동 예측
        activity_prediction = prediction_service.predict_user_activity(user_data)
        
        return {
            "user_id": user_id,
            "prediction": activity_prediction,
            "data_points": len(user_data),
            "requested_by": current_user.id
        }
        
    except Exception as e:
        logger.error(f"사용자 {user_id} 활동 예측 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/users/batch-predict")
async def batch_predict_users(
    user_ids: List[int],
    current_user: User = Depends(get_current_user)
):
    """여러 사용자 일괄 예측"""
    try:
        # 사용자 데이터 일괄 조회
        users_data = {}
        for user_id in user_ids:
            user_data = await get_user_historical_data(user_id, days=30)
            if user_data:
                users_data[user_id] = user_data
        
        if not users_data:
            raise HTTPException(status_code=404, detail="사용자 데이터를 찾을 수 없습니다")
        
        # 일괄 예측
        predictions = prediction_service.predict_batch_users(users_data)
        
        return {
            "predictions": predictions,
            "total_users": len(predictions),
            "requested_by": current_user.id,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"일괄 예측 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/trends/overview")
async def get_trends_overview(
    days: int = 7,
    current_user: User = Depends(get_current_user)
):
    """전체 트렌드 개요"""
    try:
        # 활성 사용자 조회
        active_users = await get_active_users(days=30)
        
        if not active_users:
            return {
                "message": "활성 사용자 데이터가 없습니다",
                "trends": {},
                "timestamp": datetime.now().isoformat()
            }
        
        # 샘플 사용자들에 대한 트렌드 분석
        sample_users = active_users[:10]  # 상위 10명만 분석
        users_data = {}
        
        for user_id in sample_users:
            user_data = await get_user_historical_data(user_id, days=30)
            if user_data:
                users_data[user_id] = user_data
        
        if not users_data:
            raise HTTPException(status_code=404, detail="분석할 사용자 데이터가 없습니다")
        
        # 일괄 예측
        predictions = prediction_service.predict_batch_users(users_data)
        
        # 트렌드 집계
        trend_summary = aggregate_trends(predictions)
        
        return {
            "trends": trend_summary,
            "analyzed_users": len(users_data),
            "total_active_users": len(active_users),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"트렌드 개요 생성 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/model/info")
async def get_model_info(current_user: User = Depends(get_current_user)):
    """모델 정보 조회"""
    return prediction_service.get_model_info()

async def get_user_historical_data(user_id: int, days: int = 30) -> List[Dict]:
    """사용자 과거 데이터 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            # 사용자 활동 데이터 조회
            query = """
            SELECT 
                DATE(created_at) as date,
                COUNT(*) as daily_activity,
                AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) as avg_session_length,
                COUNT(CASE WHEN chat_type = 'rag' THEN 1 END) as rag_usage,
                COUNT(CASE WHEN success = true THEN 1 END) as success_count,
                COUNT(CASE WHEN success = false THEN 1 END) as error_count
            FROM chat_messages 
            WHERE user_id = $1 
            AND created_at >= NOW() - INTERVAL $2 days
            GROUP BY DATE(created_at)
            ORDER BY date
            """
            
            rows = await conn.fetch(query, user_id, days)
            
            # 데이터 변환
            user_data = []
            for row in rows:
                user_data.append({
                    'date': row['date'].isoformat(),
                    'daily_activity': row['daily_activity'],
                    'avg_session_length': row['avg_session_length'] or 0,
                    'rag_usage': row['rag_usage'],
                    'success_count': row['success_count'],
                    'error_count': row['error_count'],
                    'total_requests': row['success_count'] + row['error_count'],
                    'success_rate': row['success_count'] / max(row['success_count'] + row['error_count'], 1)
                })
            
            return user_data
            
    except Exception as e:
        logger.error(f"사용자 {user_id} 데이터 조회 실패: {e}")
        return []

async def get_active_users(days: int = 30) -> List[int]:
    """활성 사용자 ID 목록 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            query = """
            SELECT user_id, COUNT(*) as activity_count
            FROM chat_messages 
            WHERE created_at >= NOW() - INTERVAL $1 days
            GROUP BY user_id
            ORDER BY activity_count DESC
            """
            
            rows = await conn.fetch(query)
            return [row['user_id'] for row in rows]
            
    except Exception as e:
        logger.error(f"활성 사용자 조회 실패: {e}")
        return []

def aggregate_trends(predictions: Dict[int, Dict]) -> Dict:
    """트렌드 예측 결과 집계"""
    trend_directions = {"증가": 0, "감소": 0, "유지": 0}
    churn_risks = {"높음": 0, "중간": 0, "낮음": 0}
    activity_levels = {"높음": 0, "중간": 0, "낮음": 0}
    
    total_users = len(predictions)
    
    for user_id, prediction in predictions.items():
        if "error" in prediction:
            continue
            
        # 트렌드 방향 집계
        trend_direction = prediction.get("trend", {}).get("trend_direction", "유지")
        if trend_direction in trend_directions:
            trend_directions[trend_direction] += 1
        
        # 이탈 위험 집계
        churn_risk = prediction.get("activity", {}).get("churn_risk", "낮음")
        if churn_risk in churn_risks:
            churn_risks[churn_risk] += 1
        
        # 활동 수준 집계
        activity_level = prediction.get("activity", {}).get("chat_activity_level", "낮음")
        if activity_level in activity_levels:
            activity_levels[activity_level] += 1
    
    return {
        "trend_distribution": {
            direction: count / total_users * 100 
            for direction, count in trend_directions.items()
        },
        "churn_risk_distribution": {
            risk: count / total_users * 100 
            for risk, count in churn_risks.items()
        },
        "activity_level_distribution": {
            level: count / total_users * 100 
            for level, count in activity_levels.items()
        },
        "total_analyzed_users": total_users
    }
