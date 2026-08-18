#!/bin/bash
# Write, list or clear the operator memories the agent retrieves from.
# Scope MUST carry app_name as well as user_id: VertexAiMemoryBankService
# retrieves with {app_name, user_id} and matches the map exactly, so a memory
# written with {user_id} alone is invisible to the agent.
RE=${GAO_ENGINE:-8639269128982495232}
USER_ID=${GAO_USER:-platform-ops}
TOK=$(gcloud auth print-access-token)
B="https://us-central1-aiplatform.googleapis.com/v1beta1/projects/sdl-cinema-2026/locations/us-central1/reasoningEngines/$RE"

case "$1" in
  write)
    curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
      -d "$(python3 -c "
import json,sys;print(json.dumps({'fact':sys.argv[1],'scope':{'app_name':'$RE','user_id':'$USER_ID'}}))" "$2")" \
      "$B/memories" > /dev/null && echo "wrote: $2" ;;
  list)
    curl -s -H "Authorization: Bearer $TOK" "$B/memories?pageSize=100" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ms=[m for m in d.get('memories',[]) if m.get('scope',{}).get('user_id')=='$USER_ID']
print(len(ms),'memories in scope')
for m in ms: print(' ', m['name'].split('/')[-1], repr(m.get('fact'))[:90])" ;;
  clear)
    for m in $(curl -s -H "Authorization: Bearer $TOK" "$B/memories?pageSize=100" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(' '.join(x['name'].split('/')[-1] for x in d.get('memories',[]) if x.get('scope',{}).get('user_id')=='$USER_ID'))"); do
      curl -s -X DELETE -H "Authorization: Bearer $TOK" "$B/memories/$m" > /dev/null; done
    echo "cleared" ;;
  *) echo "usage: memories.sh {write <fact>|list|clear}" ;;
esac
