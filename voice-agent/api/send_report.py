from fastapi import FastAPI
from fastapi.params import Body
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv(".env.local")

app = FastAPI()

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

@app.post("/calls/report")
async def save_report(report: dict = Body(...)):
    result = supabase.table("call_reports").insert({
        "call_id": report.get("call_id"),
        "lead_id": report.get("lead_id"),
        "data": report
    }).execute()
    return {"status": "saved", "call_id": report.get("call_id")}


# print(supabase.table('call_reports').select('*').execute())