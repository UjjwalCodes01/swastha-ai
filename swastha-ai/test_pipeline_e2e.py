"""
SwasthaAI End-to-End Pipeline Test.

Uploads a sample document via the ingestion API, then polls until
the pipeline finishes processing it. Finally queries the output API
for the AI analysis results.

Usage:
    .\.venv\Scripts\python.exe test_pipeline_e2e.py
"""
import asyncio
import json
import sys
import time

import httpx

API = "http://localhost:8000"
API_KEY = "4ed29acd03b88585355fb1d0be28a9d5"
HEADERS = {"X-API-Key": API_KEY}

import time as _time

# A small sample regulatory document (plain text simulating a drug submission)
# Unique timestamp ensures no duplicate rejection across test runs
_RUN_TS = _time.strftime("%Y%m%d-%H%M%S")
SAMPLE_DOC = f"""
DRUG SUBMISSION REPORT
======================
Application Number: APP-2026-SAE-00142
Drug Name: Atorvastatin Calcium 80mg Tablets
Manufacturer: PharmaCo India Ltd.
Date of Submission: 2026-05-10

CLINICAL SUMMARY
-----------------
This submission pertains to a Serious Adverse Event (SAE) report involving
hepatotoxicity in a 58-year-old male patient following 12 weeks of treatment
with Atorvastatin Calcium 80mg tablets.

ADVERSE EVENT DETAILS
---------------------
Event Type: Hepatotoxicity (Grade 3)
Onset: Day 84 of treatment
Patient Age: 58 years, Male
Comorbidities: Type 2 Diabetes, Hypertension
Concomitant Medications: Metformin 1000mg, Amlodipine 5mg

Laboratory Findings:
- ALT: 312 U/L (normal: 7-56 U/L) - ELEVATED
- AST: 287 U/L (normal: 10-40 U/L) - ELEVATED
- Total Bilirubin: 3.2 mg/dL (normal: 0.1-1.2 mg/dL) - ELEVATED
- ALP: 198 U/L (normal: 44-147 U/L) - ELEVATED

CAUSALITY ASSESSMENT
--------------------
Using the WHO-UMC causality assessment system, the relationship between
Atorvastatin and the hepatotoxicity event is assessed as PROBABLE.

ACTIONS TAKEN
-------------
1. Drug discontinued immediately
2. Patient hospitalized for monitoring
3. Supportive treatment with N-Acetylcysteine initiated
4. Follow-up liver function tests scheduled at 2-week intervals

OUTCOME
-------
Patient showed gradual improvement. ALT normalized after 6 weeks of
drug discontinuation. Patient was discharged after 10 days.

REPORTER INFORMATION
--------------------
Dr. Priya Sharma, MD (Internal Medicine)
Government Medical College, Mumbai
Phone: +91-22-XXXX-XXXX
Email: dr.sharma@example.com

PII NOTE: Patient name: Rajesh Kumar, Aadhaar: 1234-5678-9012
Test Run: {_RUN_TS}
"""


async def main():
    print()
    print("=" * 60)
    print("  SwasthaAI End-to-End Pipeline Test")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=30) as client:
        # ── Step 1: Health Check ──
        print("\n[1] Checking service health...")
        r = await client.get(f"{API}/api/v1/ingest/health")
        health = r.json()
        print(f"    Status: {health['status']}")
        for svc, info in health.get("services", {}).items():
            print(f"    {svc}: {info['status']}")
        if health["status"] != "healthy":
            print("    WARNING: Not all services healthy, pipeline may fail.")

        # ── Step 2: Upload Document ──
        print("\n[2] Uploading sample SAE document...")
        files = {
            "file": ("sae_report_atorvastatin.txt", SAMPLE_DOC.encode(), "text/plain"),
        }
        data = {
            "submission_type": "sae",
            "portal_source": "manual",
        }
        r = await client.post(
            f"{API}/api/v1/ingest/submission",
            files=files,
            data=data,
            headers=HEADERS,
        )
        if r.status_code != 200:
            print(f"    FAILED: {r.status_code} - {r.text[:300]}")
            sys.exit(1)

        result = r.json()
        doc_id = result.get("doc_id")
        print(f"    SUCCESS! doc_id = {doc_id}")
        print(f"    Status: {result.get('status')}")
        print(f"    Message: {result.get('message', '')}")

        # ── Step 3: Poll for Processing ──
        print("\n[3] Polling for processing status...")
        max_polls = 30
        for i in range(max_polls):
            r = await client.get(
                f"{API}/api/v1/ingest/status/{doc_id}",
                headers=HEADERS,
            )
            if r.status_code == 200:
                status_data = r.json()
                current_status = status_data.get("status", "unknown")
                print(f"    Poll {i+1}/{max_polls}: {current_status}")
                if current_status in ("processed", "reviewed", "failed", "rejected"):
                    break
            else:
                print(f"    Poll {i+1}/{max_polls}: HTTP {r.status_code}")
            await asyncio.sleep(3)
        else:
            print("    Timed out waiting for processing. Document may still be in pipeline.")

        # ── Step 4: Check Output API ──
        print("\n[4] Querying Output API for results...")
        r = await client.get(
            f"{API}/api/v1/output/submissions/{doc_id}",
            headers=HEADERS,
        )
        if r.status_code == 200:
            detail = r.json()
            print(f"    Document: {detail.get('id')}")
            print(f"    Status: {detail.get('status')}")
            summary = detail.get("executive_summary", "")
            if summary:
                print(f"    AI Summary: {summary[:200]}...")
            findings = detail.get("key_findings", [])
            if findings:
                print(f"    Key Findings ({len(findings)}):")
                for f in findings[:3]:
                    print(f"      - {f}")
            risks = detail.get("risks", [])
            if risks:
                print(f"    Risks ({len(risks)}):")
                for risk in risks[:3]:
                    print(f"      - {risk}")
            pii = detail.get("pii_entities_removed", 0)
            print(f"    PII Entities Removed: {pii}")
            compliance = detail.get("compliance_findings", [])
            print(f"    Compliance Assessments: {len(compliance)}")
            xai = detail.get("xai_log", [])
            print(f"    XAI Decision Records: {len(xai)}")
        elif r.status_code == 404:
            print("    Document not yet available in output API (processing may still be running).")
        else:
            print(f"    Output API returned: {r.status_code} - {r.text[:200]}")

        # ── Step 5: Check Dashboard Metrics ──
        print("\n[5] Checking dashboard metrics...")
        r = await client.get(f"{API}/api/v1/output/dashboard/metrics", headers=HEADERS)
        if r.status_code == 200:
            metrics = r.json()
            print(f"    Total Processed: {metrics.get('total_processed', 0)}")
            print(f"    Pending Review: {metrics.get('pending_review', 0)}")
            print(f"    Critical SAEs: {metrics.get('critical_saes', 0)}")
            print(f"    Auto-Approved: {metrics.get('auto_approved', 0)}")
        else:
            print(f"    Metrics API returned: {r.status_code}")

    print("\n" + "=" * 60)
    print("  Pipeline test complete!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
