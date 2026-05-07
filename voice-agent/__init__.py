from supabase import create_client

SUPABASE_URL = "https://rlwzvczuapajtwdgwdnt.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJsd3p2Y3p1YXBhanR3ZGd3ZG50Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODE0OTY5NSwiZXhwIjoyMDkzNzI1Njk1fQ.UbGjUlKw4Z3B0bOPBUnpmFYM9wIZovf4eUK1Kz46G4U"

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
result = sb.table("call_reports").select("*").execute()
print(result)