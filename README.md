# ai201-project4-provenance-guard
A backend system that any creative sharing platform could plug into to classify submitted content, score confidence in that classification, surface a transparency label to users, and handle appeals from creators who believe they've been misclassified.


## Milestone 5 Test 
To test that rate limiting is working, run this in a new terminal window while your Flask server is running (it sends 12 rapid requests — more than the 10/minute limit):

for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5000/submit \
    -H "Content-Type: application/json" \
    -d '{"text": "This is a test submission for rate limit testing purposes only.", "creator_id": "ratelimit-test"}'
done

$ for i in $(seq 1 12); do   curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:5000/submit     -H "Content-Type: application/json"     -d '{"text": "This is a test submission for rate limit testing purposes only. Please do not try this at home. Please play table tennis like a serious person instead. Who is JSON and who is son?", "creator_id": "ratelimit-test"}'; done
200
200
200
200
200
200
200
200
200
200
429
429
