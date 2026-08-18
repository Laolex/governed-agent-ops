#!/bin/bash
# Ask the deployed operations agent one question and print the turn.
RE=${GAO_ENGINE:-8639269128982495232}
TOK=$(gcloud auth print-access-token)
B="https://us-central1-aiplatform.googleapis.com/v1beta1/projects/sdl-cinema-2026/locations/us-central1/reasoningEngines/$RE"
USER_ID=${GAO_USER:-platform-ops}
S=$(curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d "{\"class_method\":\"create_session\",\"input\":{\"user_id\":\"$USER_ID\"}}" "$B:query" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['output']['id'])")
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json,sys
print(json.dumps({'class_method':'stream_query','input':{'user_id':'$USER_ID','session_id':'$S','message':sys.argv[1]}}))" "$1")" \
  "$B:streamQuery?alt=sse" | python3 -c "
import json,sys
for line in sys.stdin:
    line=line.strip()
    if line.startswith('data:'): line=line[5:].strip()
    if not line: continue
    try: d=json.loads(line)
    except: continue
    for p in d.get('content',{}).get('parts',[]):
        if p.get('function_call'): print('CALL:',p['function_call'].get('name'),json.dumps(p['function_call'].get('args')))
        if p.get('function_response'): print('RESP:',json.dumps(p['function_response'].get('response'))[:220])
        if p.get('text'): print('TEXT:',p['text'].strip()[:400])"
