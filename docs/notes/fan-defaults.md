# Fan default: single-zone VAV surrogate

All TMHP indoor and outdoor fan defaults use the single-zone VAV polynomial
from PNNL-26917, PRM Reference Manual, Eq. (11), Table 50 (printed pp. 3.153–3.154):

`P/P_ref = max(0.027828 + 0.026583*x - 0.087069*x² + 1.030920*x³, 0.10)`

Here `x = actual airflow / reference airflow`. The electrical floor does not
impose an airflow floor. If airflow bounds are omitted, the model uses
15–100% of reference airflow. The 15% lower bound is a retained TMHP control
assumption informed by the low-flow extrapolation example in ASHRAE 2025
Fundamentals, Chapter 19, Figure 8; it is not a universal equipment requirement.

For reference airflow 0.5 m³/s and power 50 W, these defaults give minimum
flow 0.075 m³/s and minimum active power 5 W. Different low-flow operating
points may have the same power while the HX solver continues closing heat duty.
The source coefficients sum to 0.998262 at full reference flow; TMHP preserves
that polynomial without renormalizing it.

`None` or an empty coefficient dictionary selects the new default. Explicit
coefficient dictionaries retain their existing coefficient fallback semantics
and have no electrical floor unless `power_min_ratio` is supplied. Explicit
flow limits remain independent. Use `ASHRAE_VSD_COEFFICIENTS.copy()` to retain
the previous fixed-static-pressure curve (no floor by default). The raw
`calc_ashrae_fan_power_ratio` helper still evaluates that original correlation.

This is an intentional default behavior change: fan electricity, COP and optimal
operating points can change. The common default is a modeling surrogate for all
fan types, including outdoor-coil fans; it does not assert that every device has
single-zone VAV/static-pressure-reset hardware. Manufacturer curves should be
supplied when available. Existing explicit inputs and archived figures are not
rewritten automatically.

Sources:
- https://www.pnnl.gov/main/publications/external/technical_reports/PNNL-26917.pdf
- https://handbook.ashrae.org/Handbooks/F25/IP/F25_Ch19/F25_Ch19_ip.aspx
