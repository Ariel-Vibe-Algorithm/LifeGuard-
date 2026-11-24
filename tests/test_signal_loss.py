# -*- coding: utf-8 -*-
""""אין קליטה": השרת מזהה שהטלפון הפסיק לדווח בליווי/באירוע, מעדכן את מי שעוקב, ומדווח כשהקשר חוזר."""
import json
import time

import pytest

from test_features_e2e import TLV, _c, _connect, _start


@pytest.fixture
def srv(tmp_path):
    httpd, url = _start(tmp_path)
    yield httpd, url
    httpd.shutdown()
    httpd.service.close()


def _events(httpd, user, ntype):
    return [json.loads(r["payload"]) for r in httpd.service._q(
        "SELECT payload FROM notifications WHERE username=? AND type=? ORDER BY id", (user, ntype))]


def test_escort_signal_loss_and_recovery(srv):
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    e = dana.start_escort(30, location=TLV)
    eid = e["id"] if "id" in e else e["escort"]["id"]
    svc = httpd.service
    svc.run_escalation_tick()
    assert not [x for x in _events(httpd, "mom", "escort_update") if x.get("event") == "signal_lost"]
    svc._x("UPDATE escorts SET last_loc_at=? WHERE id=?", (time.time() - svc.signal_loss_seconds - 5, eid))
    svc.run_escalation_tick()
    svc.run_escalation_tick()                      # פעם אחת בלבד, לא בכל שנייה
    lost = [x for x in _events(httpd, "mom", "escort_update") if x.get("event") == "signal_lost"]
    assert len(lost) == 1 and "אבד הקשר" in lost[0]["text"] and lost[0]["last_seen"]
    assert mom.get_escort(eid)["signal_lost_since"]
    dana.escort_location(eid, {**TLV, "lat": 32.09})
    back = [x for x in _events(httpd, "mom", "escort_update") if x.get("event") == "signal_back"]
    assert len(back) == 1 and mom.get_escort(eid)["signal_lost_since"] is None
    assert svc._q("SELECT status FROM escorts WHERE id=?", (eid,), one=True)["status"] == "active"   # לא SOS אוטומטי


def test_incident_location_stale_is_flagged_to_responders(srv):
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    inc = dana.create_incident("e", location=TLV)
    svc = httpd.service
    svc._x("UPDATE incident_locations SET received_at=? WHERE incident_id=?",
           (time.time() - svc.incident_signal_loss_seconds - 5, inc["id"]))
    svc.run_escalation_tick()
    v = mom.get_incident(inc["id"])
    assert v["signal_lost_since"] and any(e["kind"] == "signal_lost" for e in dana.get_incident(inc["id"])["timeline"])
    assert [x for x in _events(httpd, "mom", "incident_update") if x.get("event") == "signal_lost"]
    assert not [x for x in _events(httpd, "dana", "incident_update") if x.get("event") == "signal_lost"]
    dana.update_location(inc["id"], {**TLV, "lat": 32.0866, "is_precise": True})
    assert mom.get_incident(inc["id"])["signal_lost_since"] is None
