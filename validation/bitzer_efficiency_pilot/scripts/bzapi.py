"""Minimal client for the BITZER Software web backend (anonymous session)."""

import json
import time
import urllib.request
import uuid

BASE = "https://bitzer-virtual-api.germanywestcentral.cloudapp.azure.com/api/v1/"


class Session:
    def __init__(self, sid=None):
        self.sid = sid or str(uuid.uuid4())
        self.h = {
            "x-session-id": self.sid,
            "x-main-window": f"UserOptions_{self.sid}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def req(self, method, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        sep = "&" if "?" in path else "?"
        url = BASE + path + f"{sep}time={int(time.time() * 1000)}"
        for k in range(3):
            try:
                r = urllib.request.Request(url, data=data, method=method, headers=self.h)
                with urllib.request.urlopen(r, timeout=timeout) as f:
                    t = f.read().decode()
                    return json.loads(t) if t.strip() else None
            except Exception:
                if k == 2:
                    raise
                time.sleep(2)

    def controls(self, mod):
        return self.req("GET", f"calc?mod={mod}&modeAcc=false")

    def patch(self, mod, ops):
        return self.req("PATCH", f"calc?mod={mod}&modeAcc=false", ops)

    def results(self, mod, view=""):
        return self.req("GET", f"calc/results?mod={mod}&modeAcc=false&resultView={view}")

    def tab(self, mod, tab):
        return self.req("GET", f"calc/{tab}?mod={mod}&modeAcc=false")


def fields(ctrl):
    out = {}

    def walk(cs):
        for ch in cs or []:
            if ch is None:
                continue
            for f in ch.get("Fields", []) or []:
                if f:
                    out[f["Id"]] = dict(
                        name=ch.get("ElementName"),
                        type=f.get("Type"),
                        value=f.get("Value"),
                        text=f.get("Text"),
                        enabled=f.get("Enabled"),
                        visible=f.get("Visible"),
                        elements=[e.get("Value") for e in f.get("Elements", [])],
                        mn=f.get("Minimum"),
                        mx=f.get("Maximum"),
                        auto=f.get("AutoText"),
                        unit=f.get("Unit"),
                    )
            walk(ch.get("Children"))

    walk(ctrl["Controls"])
    return out


def table(res):
    """Result matrix -> {row label: [col values]}"""
    mx = res["Output"]["Mx"]
    out = {}
    for row in mx:
        if not row:
            continue
        lab = row[0]["Text1"]
        out[lab] = [(c["Text1"] + c["Text2"]).strip() for c in row[1:]]
    return out


def ctab(s, tab, extra=""):
    return s.req("GET", f"CalculationTabs/{tab}?comprName=&modeAcc=false{extra}")
