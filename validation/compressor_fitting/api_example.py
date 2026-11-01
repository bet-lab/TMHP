"""Exact Fujitsu catalogue API shown in the editable Figma code window."""

from tmhp import AirSourceHeatPump

# hp_capacity uses the constructor's cooling rating: published nominal 8.0 kW.
# Published nominal heating 8.8 kW is the separate PLR display reference.
hp = AirSourceHeatPump(
    ref="R410A",
    hp_capacity=8000.0,
    dV_iu_fan_a_rated=1380 / 3600,
    dV_ou_fan_a_rated=3600 / 3600,
    rps_min=30.0,
    rps_max=110.0,
    PR_cycle_max=7.5,
    dT_approach_bounds=(1.0, 35.0),
)
# indoor DB [°C], outdoor DB [°C], net heat [W], COP = TC/PI
# Manufacturer calculated maximum-capacity table, PDF p21 (printed p17).
points = [
    (20, 7, 11000, 11000 / 3300),
    (20, 10, 11330, 11330 / 3290),
    (20, 15, 10510, 10510 / 2640),
    (20, 20, 10430, 10430 / 2040),
]

if __name__ == "__main__":
    report = hp.fit(
        points,
        mode="heating",
        parameters=("overall_efficiency", "volumetric_efficiency", "heat_loss_fraction"),
    )
    assert report.applied, report.reason
    # Optional lambda_bounds specifies search intervals, not fitted results.
    print(report.applied_scales, report.final_cop_rmse)
