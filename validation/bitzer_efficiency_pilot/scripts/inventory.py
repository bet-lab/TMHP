"""Phase 1: which BITZER compressors accept External FI, over what frequency range, and displacement."""

import csv
import re

from bzapi import Session, ctab, fields

PLAN = [
    (
        "ESC",
        "scroll",
        ["ESC_ORBIT", "ESC_ORBITPlus", "ESC_ORBITFIT", "ESC_ORBITBoreal"],
        ["R410A", "R32", "R290", "R454B", "R134a"],
    ),
    ("HHK", "reciprocating", ["HHK_VARISPEED"], ["R134a", "R290", "R410A"]),
    ("HS", "screw", ["All"], ["R134a"]),
]


def ids(ctrl):
    m = {}
    for k, v in fields(ctrl).items():
        m.setdefault(v["name"], k.split("_")[0])
    return m


def P(e, f, v):
    return [{"op": "replace", "path": f"/{e}/{f}", "value": v}]


rows = []
for mod, ctype, series_list, refs in PLAN:
    for ser in series_list:
        for ref in refs:
            s = Session()
            c = s.controls(mod)
            I = ids(c)
            c = s.patch(mod, P(I["Refrigerant"], 1, ref))
            f = fields(c)
            if f[f"{I['Refrigerant']}_1"]["value"] != ref:
                print("refrigerant not accepted", mod, ref)
                continue
            if "Series" in I and ser != "All":
                if ser not in f[f"{I['Series']}_1"]["elements"]:
                    print("series n/a", mod, ser, ref)
                    continue
                c = s.patch(mod, P(I["Series"], 1, ser))
                I = ids(c)
            c = s.patch(mod, P(I["ComprType"], 0, True))
            I = ids(c)
            models = fields(c)[f"{I['ComprType']}_1"]["elements"]
            print(mod, ser, ref, len(models), models[:20], flush=True)
            for mdl in models:
                c = s.patch(mod, P(I["ComprType"], 1, mdl))
                I = ids(c)
                fi = I.get("VaripackPowerControl")
                rng = (None, None)
                fi_ok = False
                if fi:
                    c = s.patch(mod, P(fi, 0, True))
                    ff = fields(c)
                    fi_ok = bool(ff[f"{fi}_0"]["value"])
                    if fi_ok:
                        num = [v for k, v in ff.items() if k.startswith(f"{fi}_") and v["type"] == "Number"]
                        rng = (num[0]["mn"], num[0]["mx"]) if num else ("?", "?")
                        if not num:
                            print(
                                "    FI fields:",
                                {
                                    k: (v["type"], v["value"], v["text"])
                                    for k, v in ff.items()
                                    if k.startswith(f"{fi}_")
                                },
                            )
                    if "NoPowerControl" in I:
                        s.patch(mod, P(I["NoPowerControl"], 0, True))
                td = ctab(s, "techData")
                disp50 = disp60 = rpm50 = ""
                for grp in td.get("TechDataResultList", []):
                    for it in grp:
                        lg = it["Legend"]
                        if lg.startswith("Displacement"):
                            m = re.search(r"\((\d+)\s*rpm\s*(\d+)\s*Hz\)", lg)
                            val = it["Value"][0].replace(",", ".").split()[0]
                            if m and m.group(2) == "50":
                                disp50, rpm50 = val, m.group(1)
                            elif m and m.group(2) == "60":
                                disp60 = val
                            elif not disp50:
                                disp50 = val + " (" + lg + ")"
                rows.append(
                    dict(
                        module=mod,
                        compressor_type=ctype,
                        series=ser,
                        refrigerant=ref,
                        compressor_id=mdl,
                        external_fi=fi_ok,
                        f_min_Hz=rng[0],
                        f_max_Hz=rng[1],
                        displacement_m3h_50Hz=disp50,
                        rpm_50Hz=rpm50,
                        displacement_m3h_60Hz=disp60,
                    )
                )
                print("   ", rows[-1], flush=True)
                with open("compressor_inventory.csv", "w", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    w.writerows(rows)

with open("compressor_inventory.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
