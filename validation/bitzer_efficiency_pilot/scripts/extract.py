"""Extract BITZER operating points for one compressor over an (SST, SDT, f) grid.

usage: extract.py MODULE SERIES REFRIGERANT MODEL OUT.csv [sst list] [sdt list] [f list]
"""

import csv
import datetime
import re
import sys

from bzapi import Session, ctab, fields, table

VERSION = "BITZER Software v7.1.11.4 (web)"


def ids(ctrl):
    m = {}
    for k, v in fields(ctrl).items():
        m.setdefault(v["name"], k.split("_")[0])
    return m


def P(e, f, v):
    return [{"op": "replace", "path": f"/{e}/{f}", "value": v}]


def num(s):
    if s is None or s == "":
        return None
    m = re.search(
        r"-?\d+(?:[.,]\d+)?",
        s.replace(".", "").replace(",", ".") if s.count(",") == 1 and s.count(".") >= 1 else s.replace(",", "."),
    )
    return float(m.group()) if m else None


def TDIS(t):
    return next((k for k in t if k.startswith("Discharge gas temp")), "")


def setup(mod, ser, ref, mdl):
    s = Session()
    c = s.controls(mod)
    I = ids(c)
    s.patch(mod, P(I["Refrigerant"], 1, ref))
    if "Series" in I and ser != "All":
        I = ids(s.patch(mod, P(I["Series"], 1, ser)))
    I = ids(s.patch(mod, P(I["ComprType"], 0, True)))
    c = s.patch(mod, P(I["ComprType"], 1, mdl))
    I = ids(c)
    assert fields(c)[f"{I['ComprType']}_1"]["value"] == mdl, "model not selected"
    if I.get("TempLiquid"):
        s.patch(mod, P(I["TempLiquid"], 0, "LiquidSubcooling"))
        s.patch(mod, P(I["TempLiquid"], 1, 0))
    s.patch(mod, P(I["TempSuction"], 0, "SuctionSuperheat"))
    s.patch(mod, P(I["TempSuction"], 1, 10))
    if "VaripackPowerControl" in I:  # external FI (ORBIT scroll, HS screw)
        c = s.patch(mod, P(I["VaripackPowerControl"], 0, True))
        I = ids(c)
        I["_freq"], I["_mode"] = I["VaripackPowerControl"], "External FI"
    else:  # integrated FI (VARISPEED reciprocating)
        I["_freq"], I["_mode"] = I["Motorfrequence"], "integrated FI (VARISPEED)"
    return s, I, fields(c)


def run(mod, ser, ref, mdl, out, ssts, sdts, freqs, meta):
    s, I, f0 = setup(mod, ser, ref, mdl)
    fi = I["_freq"]
    td = ctab(s, "techData")
    disp50 = rpm50 = None
    for grp in td.get("TechDataResultList", []):
        for it in grp:
            m = re.search(r"Displacement \((\d+)\s*(?:rpm|min-1|1/min)\s*50 ?Hz\)", it["Legend"])
            if m:
                disp50 = float(it["Value"][0].split()[0].replace(",", "."))
                rpm50 = float(m.group(1))
    rows = []
    date = datetime.date.today().isoformat()
    for te in ssts:
        s.patch(mod, P(I["Temp0"], 1, te))
        for tc in sdts:
            s.patch(mod, P(I["TempCond"], 1, tc))
            for hz in freqs:
                s.patch(mod, P(fi, 2, hz))
                res = s.results(mod)
                o = res["Output"]
                t = table(res) if o.get("Mx") and o["Mx"][0] else {}

                def g(k, t=t):
                    return (t.get(k) or [""])[0]

                row = dict(
                    manufacturer="BITZER",
                    software_name="BITZER Software (web)",
                    software_version=VERSION,
                    extraction_date=date,
                    compressor_id=mdl,
                    compressor_family=ser,
                    compressor_type=meta["type"],
                    refrigerant=ref,
                    capacity_control_mode=I["_mode"],
                    frequency_Hz=num(g("Compressor frequency")) if g("Compressor frequency") else None,
                    frequency_requested_Hz=hz,
                    rpm_if_available="",
                    rated_frequency_Hz=50,
                    rated_rpm_if_available=rpm50,
                    displacement_m3_rev=(disp50 / 3600 / (rpm50 / 60)) if disp50 else None,
                    displacement_m3_h_if_reported=disp50,
                    T_evap_C=te,
                    T_cond_C=tc,
                    superheat_K=10,
                    subcooling_K=0,
                    capacity_W=(num(g("Cooling capacity")) or 0) * 1000 if g("Cooling capacity") else None,
                    power_input_W=(num(g("Power input")) or 0) * 1000 if g("Power input") else None,
                    mass_flow_kg_s=(num(g("Mass flow")) / 3600) if g("Mass flow") else None,
                    T_discharge_C=num(g(TDIS(t))) if TDIS(t) else None,
                    T_discharge_label=TDIS(t),
                    condenser_capacity_W=(num(g("Condenser capacity")) or 0) * 1000
                    if g("Condenser capacity")
                    else None,
                    current_label=[k for k in t if k.startswith("Current")][:1]
                    and [k for k in t if k.startswith("Current")][0],
                    current_A=num(g([k for k in t if k.startswith("Current")][0]))
                    if any(k.startswith("Current") for k in t)
                    else None,
                    BITZER_warning=" | ".join(x for x in (o.get("FatalErrorTxt") or []) if x)
                    + (" | " + " | ".join(o.get("NormTxt") or []) if o.get("NormTxt") else ""),
                    BITZER_limit_status=o.get("FatalErrorNr"),
                    source_screen_or_export="api/v1/calc/results (same backend as web UI Result tab)",
                )
                rows.append(row)
    new = not __import__("os").path.exists(out)
    with open(out, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        if new:
            w.writeheader()
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    mod, ser, ref, mdl, out = sys.argv[1:6]

    def L(i, d):
        return [float(x) for x in (sys.argv[i].split(",") if len(sys.argv) > i else d)]

    rows = run(
        mod,
        ser,
        ref,
        mdl,
        out,
        L(6, [-15, -10, -5, 0, 5]),
        L(7, [35, 40, 45, 50, 55]),
        L(8, [35, 40, 45, 50, 55, 60, 65, 70, 75]),
        {"type": sys.argv[9] if len(sys.argv) > 9 else "scroll"},
    )
    ok = sum(1 for r in rows if r["BITZER_limit_status"] == 0)
    print(mdl, ref, len(rows), "ok", ok)
