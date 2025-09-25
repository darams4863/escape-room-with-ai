"""비즈니스 인사이트 데이터 저장소"""

import json
from typing import Any, Dict, List

from ..core.connections import postgres_manager
from ..core.logger import logger
from ..models.analytics import PopularRegion, PopularTheme, UserTrend


async def log_analytics_event(
    user_id: int, 
    session_id: str, 
    event_type: str, 
    region: str = None, 
    theme: str = None, 
    engagement_score: float = 0.5, 
    info: dict = None
) -> bool:
    """분석 이벤트 로깅 (확장성 고려한 JSONB 기반)"""
    try:
        async with postgres_manager.get_connection() as conn:
            await conn.execute(
                """
                    INSERT INTO analytics_events (user_id, session_id, event_type, region, theme, engagement_score, info)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                """, 
                user_id, session_id, event_type, region, theme, 
                engagement_score, json.dumps(info or {})
            )
            return True
    except Exception as e:
        logger.error(f"Failed to log analytics event: {e}")
        return False


async def get_popular_regions(days: int = 7) -> List[PopularRegion]:
    """인기 지역 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            rows = await conn.fetch(
                """
                    SELECT 
                        region,
                        COUNT(*) as mention_count,
                        ROUND(COUNT(*) * 100.0 / (
                            SELECT 
                                COUNT(*)
                            FROM analytics_events 
                            WHERE event_type = 'chat_request' 
                            AND created_at >= NOW() - INTERVAL '%s days'), 2
                        ) as percentage
                    FROM analytics_events
                    WHERE event_type = 'chat_request'
                    AND created_at >= NOW() - INTERVAL '%s days'
                    AND region IS NOT NULL
                    GROUP BY region
                    ORDER BY mention_count DESC
                    LIMIT 10
                """ % (days, days)
            )
                
            return [PopularRegion(
                region=row['region'],
                mention_count=row['mention_count'],
                percentage=row['percentage'],
                trend='stable'  # 현재는 stable로 고정, 향후 트렌드 계산 로직 추가 예정
            ) for row in rows]
    except Exception as e:
        logger.error(f"Failed to get popular regions: {e}")
        return []


async def get_popular_themes(days: int = 7) -> List[PopularTheme]:
    """인기 테마 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            rows = await conn.fetch(
                """
                    SELECT 
                        theme,
                        COUNT(*) as mention_count,
                        ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM analytics_events WHERE event_type = 'chat_request' AND created_at >= NOW() - INTERVAL '%s days'), 2) as percentage
                    FROM analytics_events
                    WHERE event_type = 'chat_request'
                    AND created_at >= NOW() - INTERVAL '%s days'
                    AND theme IS NOT NULL
                    GROUP BY theme
                    ORDER BY mention_count DESC
                    LIMIT 10
                """ % (days, days)
            )
            
            return [PopularTheme(
                theme=row['theme'],
                mention_count=row['mention_count'],
                percentage=row['percentage'],
                trend='stable'  # 현재는 stable로 고정, 향후 트렌드 계산 로직 추가 예정
            ) for row in rows]
    except Exception as e:
        logger.error(f"Failed to get popular themes: {e}")
        return []


async def get_user_trends(days: int = 7) -> List[UserTrend]:
    """사용자 트렌드 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            # 평균 세션 길이 (JSONB에서 응답 시간 추출)
            avg_session_length = await conn.fetchval(
                """
                    SELECT AVG((info->>'response_time_ms')::float) / 1000 / 60 FROM analytics_events
                    WHERE event_type = 'chat_request'
                    AND created_at >= NOW() - INTERVAL '%s days'
                    AND info->>'response_time_ms' IS NOT NULL
                """ % days
            )
            
            # 평균 메시지 길이 (JSONB에서 추출)
            avg_message_length = await conn.fetchval(
                """
                    SELECT AVG((info->>'message_length')::int) FROM analytics_events
                    WHERE event_type = 'chat_request'
                    AND created_at >= NOW() - INTERVAL '%s days'
                    AND info->>'message_length' IS NOT NULL
                """ % days
            )
            
            # 평균 참여도 점수
            avg_engagement_score = await conn.fetchval(
                """
                    SELECT AVG(engagement_score) FROM analytics_events
                    WHERE event_type = 'chat_request'
                    AND created_at >= NOW() - INTERVAL '%s days'
                """ % days
            )
            
            return [
                UserTrend(
                    metric="avg_session_length",
                    value=float(avg_session_length or 0),
                    period=f"{days}days",
                    trend="stable"
                ),
                UserTrend(
                    metric="avg_message_length",
                    value=float(avg_message_length or 0),
                    period=f"{days}days",
                    trend="stable"
                ),
                UserTrend(
                    metric="avg_engagement_score",
                    value=float(avg_engagement_score or 0),
                    period=f"{days}days",
                    trend="stable"
                )
            ]
    except Exception as e:
        logger.error(f"Failed to get user trends: {e}")
        return []




async def get_session_quality_data(days: int = 30) -> List[Dict[str, Any]]:
    """세션 품질 분석을 위한 데이터 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            rows = await conn.fetch(
                """
                    SELECT 
                        ae.user_id,
                        ae.session_id,
                        ae.event_type,
                        ae.created_at,
                        ae.region,
                        ae.theme,
                        ae.engagement_score,
                        ae.info,
                        cs.conversation_history,
                        up.experience_level,
                        up.preferred_difficulty,
                        up.preferred_activity_level
                    FROM analytics_events ae
                    LEFT JOIN chat_sessions cs ON ae.session_id = cs.session_id
                    LEFT JOIN user_preferences up ON ae.user_id = up.user_id
                    WHERE ae.created_at >= NOW() - INTERVAL '%s days'
                    AND ae.event_type IN ('chat_request', 'recommendation_response')
                    ORDER BY ae.user_id, ae.session_id, ae.created_at
                """ % days
            )
            
            # 세션별로 그룹화
            sessions = {}
            for row in rows:
                session_key = f"{row['user_id']}_{row['session_id']}"
                if session_key not in sessions:
                    sessions[session_key] = {
                        'user_id': row['user_id'],
                        'session_id': row['session_id'],
                        'experience_level': row['experience_level'],
                        'preferred_difficulty': row['preferred_difficulty'],
                        'preferred_activity_level': row['preferred_activity_level'],
                        'messages': [],
                        'actions': [],
                        'start_time': row['created_at'],
                        'end_time': row['created_at']
                    }
                
                # JSONB 데이터 파싱
                info_data = {}
                if row['info']:
                    try:
                        info_data = json.loads(row['info']) if isinstance(row['info'], str) else row['info']
                    except:
                        info_data = {}
                
                sessions[session_key]['actions'].append({
                    'action': row['event_type'],
                    'timestamp': row['created_at'],
                    'region': row['region'],
                    'theme': row['theme'],
                    'engagement_score': row['engagement_score'],
                    'message_length': info_data.get('message_length', 0),
                    'response_time_ms': info_data.get('response_time_ms', 0),
                    'daily_chat_count': info_data.get('daily_chat_count', 0),
                    'info': info_data
                })
                
                if row['conversation_history']:
                    import json
                    try:
                        conv_data = json.loads(row['conversation_history'])
                        if 'messages' in conv_data:
                            sessions[session_key]['messages'] = conv_data['messages']
                    except:
                        pass
                
                # 세션 시간 업데이트
                if row['created_at'] > sessions[session_key]['end_time']:
                    sessions[session_key]['end_time'] = row['created_at']
            
            return list(sessions.values())
            
    except Exception as e:
        logger.error(f"Failed to get session quality data: {e}")
        return []


async def get_user_recommendation_history(
    user_id: int, 
    days: int = 30
) -> List[Dict[str, Any]]:
    """사용자별 추천 이력 조회 (개인화 추천용)"""
    try:
        async with postgres_manager.get_connection() as conn:
            rows = await conn.fetch(
                """
                    SELECT 
                        rl.room_id,
                        rl.rank_position,
                        rl.created_at,
                        er.name,
                        er.theme,
                        er.region,
                        er.difficulty_level,
                        er.rating,
                        er.price_per_person
                    FROM recommendation_logs rl
                    JOIN escape_rooms er ON rl.room_id = er.id
                    WHERE rl.user_id = $1
                    AND rl.created_at >= NOW() - INTERVAL $2 days
                    ORDER BY rl.created_at DESC
                """, 
                user_id, 
                days
            )
            
            return [dict(row) for row in rows]
            
    except Exception as e:
        logger.error(f"Failed to get user recommendation history: {e}")
        return []


async def get_trend_prediction_data(months: int = 12) -> Dict[str, Any]:
    """트렌드 예측을 위한 월별 데이터 조회"""
    try:
        async with postgres_manager.get_connection() as conn:
            # 월별 인기 테마 데이터
            theme_rows = await conn.fetch(
                """
                    SELECT 
                        DATE_TRUNC('month', ua.created_at) as month,
                        ua.theme,
                        COUNT(*) as mention_count
                    FROM analytics_events ua
                    WHERE ua.event_type = 'chat_request'
                    AND ua.created_at >= NOW() - INTERVAL '%s months'
                    AND ua.theme IS NOT NULL
                    GROUP BY month, theme
                    ORDER BY month, mention_count DESC
                """ % months
            )

            
            # 월별 인기 지역 데이터
            region_rows = await conn.fetch(
                """
                    SELECT 
                        DATE_TRUNC('month', ua.created_at) as month,
                        ua.region,
                        COUNT(*) as mention_count
                    FROM analytics_events ua
                    WHERE ua.event_type = 'chat_request'
                    AND ua.created_at >= NOW() - INTERVAL '%s months'
                    AND ua.region IS NOT NULL
                    GROUP BY month, region
                    ORDER BY month, mention_count DESC
                """ % months
            )
            
            return {
                'themes': [dict(row) for row in theme_rows],
                'regions': [dict(row) for row in region_rows]
            }
            
    except Exception as e:
        logger.error(f"Failed to get trend prediction data: {e}")
        return {'themes': [], 'regions': []}
