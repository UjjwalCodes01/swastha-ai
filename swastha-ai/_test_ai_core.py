"""Test full pipeline including AI Core."""
import asyncio
import os
import httpx

async def test():
    async with httpx.AsyncClient(timeout=60) as c:
        # Upload a test doc
        r = await c.post(
            "http://localhost:8000/api/v1/ingest/submission",
            headers={"X-API-Key": os.environ.get("API_KEY", "test_key")},
            files={"file": ("ai_core_test.txt", b"Patient experienced severe adverse reaction to Metformin 500mg. Event: Lactic acidosis. Outcome: Hospitalized. Causality: Probable. Drug was discontinued. Patient age 67 years, female. Onset: 3 days after starting therapy. New report number: 123456789.", "text/plain")},
            data={"submission_type": "sae", "portal_source": "manual"},
        )
        doc_id = r.json().get("doc_id")
        print(f"Upload: {r.status_code} doc_id={doc_id}")

        # Wait for full processing (preprocessing + AI core)
        for i in range(40):
            await asyncio.sleep(3)
            r2 = await c.get(
                f"http://localhost:8000/api/v1/output/submissions/{doc_id}",
                headers={"X-API-Key": os.environ.get("API_KEY", "test_key")},
            )
            if r2.status_code != 200:
                print(f"Poll {i+1}: HTTP {r2.status_code}")
                continue

            data = r2.json()
            summary = data.get("executive_summary", "")
            status = data.get("status", "?")

            if summary:
                print(f"Poll {i+1}: Has AI summary!")
                print(f"  Status: {status}")
                print(f"  Summary: {summary[:300]}")
                kf = data.get("key_findings", [])
                print(f"  Key findings ({len(kf)}): {kf[:3]}")
                print(f"  Risks: {data.get('risks', [])}")
                print(f"  Model: {data.get('summary_model_id', '')}")
                print(f"  Confidence: {data.get('summary_confidence', 0)}")
                print(f"  XAI records: {len(data.get('xai_log', []))}")
                print(f"  Compliance: {len(data.get('compliance_findings', []))}")
                break
            elif status == "processed":
                print(f"Poll {i+1}: processed but no AI summary yet")
            else:
                print(f"Poll {i+1}: {status}")
        else:
            print("Timed out - no AI summary appeared")
            # Print what we have
            print(f"Final status: {data.get('status')}")
            print(f"Summary field: '{data.get('executive_summary', '')}'")

asyncio.run(test())
