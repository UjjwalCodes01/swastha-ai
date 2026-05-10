"""
SwasthaAI Full Integration Test
Tests all services and pipeline layers.
"""
import asyncio
import json
import sys


async def test_all():
    results = {}

    # 1. PostgreSQL
    try:
        import asyncpg
        conn = await asyncpg.connect(
            "postgresql://swastha-ai:swastha_ai_secret@localhost:5433/swastha_ai_db"
        )
        sql = "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        tables = await conn.fetch(sql)
        results["postgres"] = {
            "status": "OK",
            "tables": [r["tablename"] for r in tables],
        }
        await conn.close()
    except Exception as e:
        results["postgres"] = {"status": "FAIL", "error": str(e)[:200]}

    # 2. Redis
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url("redis://:swastha_ai_redis_secret@localhost:6380/0")
        await r.ping()
        await r.set("swastha_test", "ok", ex=10)
        val = await r.get("swastha_test")
        results["redis"] = {"status": "OK", "ping": "PONG", "rw_test": val.decode()}
        await r.aclose()
    except Exception as e:
        results["redis"] = {"status": "FAIL", "error": str(e)[:200]}

    # 3. MinIO
    try:
        import aiobotocore.session
        import aiobotocore.config
        session = aiobotocore.session.get_session()
        async with session.create_client(
            "s3",
            endpoint_url="http://localhost:9000",
            aws_access_key_id="swastha_ai_minio",
            aws_secret_access_key="swastha_ai_minio_secret",
            region_name="us-east-1",
        ) as client:
            resp = await client.list_buckets()
            buckets = [b["Name"] for b in resp.get("Buckets", [])]
        results["minio"] = {"status": "OK", "buckets": buckets}
    except Exception as e:
        results["minio"] = {"status": "FAIL", "error": str(e)[:200]}

    # 4. Kafka
    try:
        from aiokafka.admin import AIOKafkaAdminClient
        admin = AIOKafkaAdminClient(bootstrap_servers="localhost:29092")
        await admin.start()
        topics = await admin.list_topics()
        results["kafka"] = {"status": "OK", "topics": sorted(list(topics))[:15]}
        await admin.close()
    except Exception as e:
        results["kafka"] = {"status": "FAIL", "error": str(e)[:200]}

    # 5. ChromaDB
    try:
        import chromadb
        client = chromadb.HttpClient(host="localhost", port=8002)
        results["chromadb"] = {"status": "OK"}
    except Exception as e:
        results["chromadb"] = {
            "status": "NOT RUNNING",
            "fix": "Add chromadb service to docker-compose.yml",
            "error": str(e)[:100],
        }

    # 6. lz4
    try:
        import lz4
        results["lz4"] = {"status": "OK", "version": lz4.__version__}
    except Exception as e:
        results["lz4"] = {"status": "FAIL", "error": str(e)[:100]}

    # 7. API Health endpoint
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get("http://localhost:8000/api/v1/ingest/health")
            data = resp.json()
            results["api_health"] = {
                "status": "OK" if resp.status_code == 200 else "WARN",
                "http_code": resp.status_code,
                "services": data.get("services", {}),
            }
    except Exception as e:
        results["api_health"] = {"status": "FAIL", "error": str(e)[:200]}

    # 8. API Docs endpoint
    try:
        import httpx
        async with httpx.AsyncClient() as c:
            resp = await c.get("http://localhost:8000/docs", timeout=5)
            results["swagger_ui"] = {
                "status": "OK" if resp.status_code == 200 else "FAIL",
                "http_code": resp.status_code,
            }
    except Exception as e:
        results["swagger_ui"] = {"status": "FAIL", "error": str(e)[:100]}

    # 9. Test Kafka producer (send a test message)
    try:
        from aiokafka import AIOKafkaProducer
        producer = AIOKafkaProducer(
            bootstrap_servers="localhost:29092",
            acks="all",
        )
        await producer.start()
        await producer.send_and_wait(
            "documents.raw",
            value=json.dumps({"test": "integration_check"}).encode(),
        )
        await producer.stop()
        results["kafka_produce"] = {"status": "OK", "message": "Test message sent to documents.raw"}
    except Exception as e:
        results["kafka_produce"] = {"status": "FAIL", "error": str(e)[:200]}

    # 10. Check DB schema tables
    try:
        import asyncpg
        conn = await asyncpg.connect(
            "postgresql://swastha-ai:swastha_ai_secret@localhost:5433/swastha_ai_db"
        )
        expected = ["submissions", "documents", "audit_logs", "api_keys"]
        sql_check = "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        rows = await conn.fetch(sql_check)
        actual = {r["tablename"] for r in rows}
        missing = [t for t in expected if t not in actual]
        results["db_schema"] = {
            "status": "OK" if not missing else "WARN",
            "tables_found": sorted(list(actual)),
            "missing_expected": missing,
        }
        await conn.close()
    except Exception as e:
        results["db_schema"] = {"status": "FAIL", "error": str(e)[:200]}

    return results


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  SwasthaAI Integration Test Suite")
    print("=" * 60 + "\n")
    results = asyncio.run(test_all())
    for service, result in results.items():
        status = result.get("status", "?")
        icon = "[OK]" if status == "OK" else ("[WARN]" if status in ("WARN", "NOT RUNNING") else "[FAIL]")
        print(f"{icon} {service.upper():20s}: {status}")
        for k, v in result.items():
            if k != "status":
                print(f"   {k}: {v}")
        print()
    print("=" * 60)
    fails = [k for k, v in results.items() if v.get("status") == "FAIL"]
    if fails:
        print(f"  FAILED: {', '.join(fails)}")
        sys.exit(1)
    else:
        print("  All critical services reachable!")
    print("=" * 60 + "\n")
