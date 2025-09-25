#!/usr/bin/env python3
"""
로컬 개발용 FastAPI 실행 스크립트
- FastAPI만 로컬에서 실행
- PostgreSQL, Redis, RabbitMQ는 도커에서 실행
"""

import subprocess

# import os
import sys
import time

# from pathlib import Path

def check_docker_containers():
    """도커 컨테이너 상태 확인"""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}"],
            capture_output=True,
            text=True
        )
        
        containers = result.stdout.strip().split('\n')[1:]  # 헤더 제거
        required_containers = [
            "escape_room_postgres",
            "escape_room_redis", 
            "escape_room_rabbitmq"
        ]
        
        running_containers = [line.split('\t')[0] for line in containers if 'Up' in line]
        
        print("🔍 도커 컨테이너 상태:")
        for container in required_containers:
            if container in running_containers:
                print(f"  ✅ {container}: 실행 중")
            else:
                print(f"  ❌ {container}: 중지됨")
        
        return all(container in running_containers for container in required_containers)
        
    except Exception as e:
        print(f"❌ 도커 상태 확인 실패: {e}")
        return False

def start_required_containers():
    """필요한 도커 컨테이너 시작 (스마트 재시작)"""
    print("🚀 필요한 도커 컨테이너 시작 중...")
    
    try:
        # 기존 컨테이너가 있는지 확인
        result = subprocess.run([
            "docker-compose", "ps", "-q"
        ], capture_output=True, text=True)
        
        if result.stdout.strip():
            print("🔄 기존 컨테이너 발견. 재시작 중...")
            # 기존 컨테이너 중지 및 제거
            subprocess.run([
                "docker-compose", "down"
            ], capture_output=True)
            
            # 컨테이너 다시 시작 (이미지 재빌드)
            print("🔨 컨테이너 재빌드 및 시작 중...")
            subprocess.run([
                "docker-compose", "up", "-d", "--build",
                "postgres",
                "redis", 
                "rabbitmq",
                "prometheus",
                "grafana"
            ], check=True)
        else:
            print("🆕 새 컨테이너 시작 중...")
            # 새로 시작
            subprocess.run([
                "docker-compose", "up", "-d",
                "postgres",
                "redis", 
                "rabbitmq",
                "prometheus",
                "grafana"
            ], check=True)
        
        print("✅ 도커 컨테이너 시작 완료")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"❌ 도커 컨테이너 시작 실패: {e}")
        return False

def wait_for_services():
    """서비스 준비 대기"""
    print("⏳ 서비스 준비 대기 중...")
    time.sleep(10)  # 10초 대기

# def run_fastapi():
#     """로컬에서 FastAPI 실행"""
#     print("🚀 로컬 FastAPI 시작 중...")
    
#     # 가상환경 Python 경로
#     venv_python = Path.cwd() / "venv" / "bin" / "python"
    
#     # 환경 변수 설정
#     env = os.environ.copy()
#     env["PYTHONPATH"] = str(Path.cwd())
    
#     try:
#         # 가상환경의 Python을 직접 사용
#         subprocess.run([
#             str(venv_python), "-m", "uvicorn",
#             "app.main:app",
#             "--host", "0.0.0.0",
#             "--port", "8000",
#             "--reload",
#             "--log-level", "debug"
#         ], env=env)
        
#     except KeyboardInterrupt:
#         print("\n🛑 FastAPI 종료됨")
#     except Exception as e:
#         print(f"❌ FastAPI 실행 실패: {e}")

def main():
    """메인 실행 함수"""
    print("🎯 로컬 개발 환경 시작")
    print("=" * 50)
    
    # 1. 도커 컨테이너 상태 확인
    if not check_docker_containers():
        print("\n🔧 필요한 컨테이너가 실행되지 않음. 시작 중...")
        if not start_required_containers():
            print("❌ 도커 컨테이너 시작 실패")
            sys.exit(1)
    
    # 2. 서비스 준비 대기
    wait_for_services()
    
    # 3. 완료 메시지
    print("\n🎉 모든 서비스 준비 완료!")
    print("📈 Prometheus: http://localhost:9090")
    print("📊 Grafana: http://localhost:3000")
    print("🐘 PostgreSQL: localhost:5433")
    print("🔴 Redis: localhost:6379")
    print("🐰 RabbitMQ: localhost:15672")
    print("=" * 50)
    # run_fastapi()

if __name__ == "__main__":
    main()
