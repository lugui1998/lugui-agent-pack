def encode_request(request_id, payload):
    return {"protocol": "v2", "request_id": request_id, "body": payload}
