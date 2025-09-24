"""비즈니스 인사이트 API - 과거 데이터 분석 및 통계"""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from ..core.logger import logger
from ..services.analytics_service import (
    get_business_insights,
    get_personalized_recommendations,
    predict_monthly_trends,
    predict_session_quality,
)

router = APIRouter(prefix="/analytics", tags=["Business Analytics"])


@router.get("/insights")
async def get_insights() -> Dict[str, Any]:
    """비즈니스 인사이트 조회"""
    try:
        insights = await get_business_insights()
        return {
            "status": "success",
            "data": insights
        }
    except Exception as e:
        logger.error(f"Failed to get business insights: {e}")
        raise HTTPException(status_code=500, detail="인사이트 조회에 실패했습니다.")


@router.get("/insights/summary")
async def get_insights_summary() -> Dict[str, Any]:
    """비즈니스 인사이트 요약"""
    try:
        insights = await get_business_insights()
        
        # 간단한 요약 생성
        summary = {
            "total_regions": len(insights.get("popular_regions", [])),
            "total_themes": len(insights.get("popular_themes", [])),
            "top_region": insights.get("popular_regions", [{}])[0].get("region", "N/A") if insights.get("popular_regions") else "N/A",
            "top_theme": insights.get("popular_themes", [{}])[0].get("theme", "N/A") if insights.get("popular_themes") else "N/A",
            "generated_at": insights.get("generated_at")
        }
        
        return {
            "status": "success",
            "data": summary
        }
    except Exception as e:
        logger.error(f"Failed to get insights summary: {e}")
        raise HTTPException(status_code=500, detail="인사이트 요약 조회에 실패했습니다.")


@router.get("/session-quality/{user_id}/{session_id}")
async def get_session_quality_prediction(user_id: int, session_id: str) -> Dict[str, Any]:
    """세션 품질 예측 - 채팅에서 방탈출 추천 성공 여부 예측"""
    try:
        prediction = await predict_session_quality(user_id, session_id)
        return {
            "status": "success",
            "data": prediction
        }
    except Exception as e:
        logger.error(f"Failed to predict session quality: {e}")
        raise HTTPException(status_code=500, detail="세션 품질 예측에 실패했습니다.")


@router.get("/personalized/{user_id}")
async def get_personalized_recommendations_api(user_id: int) -> Dict[str, Any]:
    """개인화 추천 생성"""
    try:
        recommendations = await get_personalized_recommendations(user_id)
        return {
            "status": "success",
            "data": recommendations
        }
    except Exception as e:
        logger.error(f"Failed to get personalized recommendations: {e}")
        raise HTTPException(status_code=500, detail="개인화 추천 생성에 실패했습니다.")


@router.get("/trends/monthly")
async def get_monthly_trend_prediction() -> Dict[str, Any]:
    """월별 트렌드 예측 - 다음 달 인기 테마/지역 예측"""
    try:
        trends = await predict_monthly_trends()
        return {
            "status": "success",
            "data": trends
        }
    except Exception as e:
        logger.error(f"Failed to predict monthly trends: {e}")
        raise HTTPException(status_code=500, detail="월별 트렌드 예측에 실패했습니다.")
