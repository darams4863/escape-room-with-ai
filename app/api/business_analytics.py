"""비즈니스 인사이트 API - 과거 데이터 분석 및 통계"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from ..services.analytics_service import get_business_insights
from ..core.logger import logger

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
