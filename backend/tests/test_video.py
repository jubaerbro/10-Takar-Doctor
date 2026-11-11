import json

import jwt

from app import core, video

SECRET = "livekit-test-secret-that-is-long-enough"


def enable_video(monkeypatch):
    monkeypatch.setattr(core.config, "livekit_url", "wss://example-project.livekit.cloud")
    monkeypatch.setattr(core.config, "livekit_api_key", "APItestkey")
    monkeypatch.setattr(core.config, "livekit_api_secret", SECRET)


def test_no_video_details_until_livekit_is_configured(api):
    patient, _ = api.patient()
    doctor, _ = api.doctor()
    c = api.ok(api.post("/match", patient, {"problem": "fever"}))
    assert api.ok(api.post(f"/consultations/{c['id']}/accept", doctor))["video"] is None


def test_each_side_gets_a_token_for_this_call_only(api, monkeypatch):
    enable_video(monkeypatch)
    closed = []
    monkeypatch.setattr(video, "_delete_room", closed.append)
    patient, patient_id = api.patient()
    doctor, doctor_id = api.doctor()
    c = api.ok(api.post("/match", patient, {"problem": "fever"}))
    assert c["video"] is None                                    # nothing before the doctor accepts
    for_doctor = api.ok(api.post(f"/consultations/{c['id']}/accept", doctor))["video"]
    for_patient = api.ok(api.get(f"/consultations/{c['id']}", patient))["video"]
    assert for_doctor["url"] == for_patient["url"] == "wss://example-project.livekit.cloud"
    assert for_doctor["room"] == for_patient["room"] == c["id"]

    seen = {}
    for who, creds, user_id in (("doctor", for_doctor, doctor_id), ("patient", for_patient, patient_id)):
        claims = jwt.decode(creds["token"], SECRET, algorithms=["HS256"], options={"verify_exp": False, "verify_nbf": False})
        assert claims["iss"] == "APItestkey" and claims["sub"] == user_id
        assert claims["video"] == {"room": c["id"], "roomJoin": True, "canPublish": True, "canSubscribe": True,
                                   "canPublishSources": ["camera", "microphone"]}
        seen[who] = claims
    started = api.ok(api.get(f"/consultations/{c['id']}", patient))["started_at"]
    assert seen["patient"]["exp"] == int(started + 600 + 30)    # the token dies with the call's time limit
    assert SECRET not in json.dumps([for_doctor, for_patient])

    api.wait(60, doctor)
    ended = api.ok(api.post(f"/consultations/{c['id']}/end", patient))
    assert ended["video"] is None
    for _ in range(50):                                          # the room is closed in the background
        if closed:
            break
        import time; time.sleep(0.02)
    assert closed == [c["id"]]


def test_room_is_closed_through_the_livekit_api(api, monkeypatch):
    enable_video(monkeypatch)
    request = video.delete_room_request("room-123")
    assert request.full_url == "https://example-project.livekit.cloud/twirp/livekit.RoomService/DeleteRoom"
    assert request.get_method() == "POST" and json.loads(request.data) == {"room": "room-123"}
    token = request.get_header("Authorization").removeprefix("Bearer ")
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    assert claims["video"] == {"roomCreate": True} and claims["iss"] == "APItestkey"
