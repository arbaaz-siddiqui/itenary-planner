cd C:\Users\MohdArbaazSiddiqui\Downloads\AI_itinerary_planner\voice_agent\voice-care\voicecare-api
$AK = (Get-Content .env | Select-String '^VAPI_API_KEY=').ToString().Split('=',2)[1].Trim('"')
$AID = (Get-Content .env | Select-String '^VAPI_ASSISTANT_ID=').ToString().Split('=',2)[1].Trim('"')
$PN = (Get-Content .env | Select-String '^VAPI_PHONE_NUMBER_ID=').ToString().Split('=',2)[1].Trim('"')
$body = @{ assistantId=$AID; phoneNumberId=$PN; customer=@{ number="+918881310786" } } | ConvertTo-Json
curl.exe -s -X POST "https://api.vapi.ai/call/phone" -H "Authorization: Bearer $AK" -H "Content-Type: application/json" -d $body


To call a different number: change +918881310786.
To schedule for later: add schedulePlan — $body = @{ assistantId=$AID; phoneNumberId=$PN; customer=@{number="+918881310786"}; schedulePlan=@{earliestAt="2026-06-22T18:30:00Z"} } | ConvertTo-Json (UTC, 2+ min ahead).


The 4 things that must stay running (each in its own terminal):

#	What	Command	Port
1	Postgres	docker compose up -d (in voice-care/)	5433
2	Planner brain	uvicorn surfaces.voice_app:app --port 8100 (in repo root)	8100
3	VoiceCare API	npm run start (in voicecare-api/)	3000
4	ngrok	ngrok http 3000	—


cd C:\Users\MohdArbaazSiddiqui\Downloads\AI_itinerary_planner\voice_agent\voice-care
.\call.ps1                    # default number
.\call.ps1 +919876543210      # other number
.\call.ps1 +918881310786 "2026-06-22T19:00:00Z"   # schedule (UTC)


 If you restart ngrok, its URL changes → tell me the new one (or update Vapi's assistant model.url to https://NEW-URL/api/webhook).



 1. Voice service (the brain + Vapi webhook, port 8100):


cd C:\Users\MohdArbaazSiddiqui\Downloads\AI_itinerary_planner
uvicorn voice_service:app --host 127.0.0.1 --port 8100
2. ngrok → 8100 (so Vapi can reach it):


ngrok http 8100
3. Streamlit UI:


streamlit run surfaces/streamlit_app.py