"""Catalogue input shown in the editable Figma code window."""

from case import CALIBRATION_BOUNDS as bounds
from case import EQUIPMENT_SETTINGS as equipment

from tmhp import AirSourceHeatPump
from tmhp.compressor_baseline import ossorio_r290_efficiencies

hp = AirSourceHeatPump(**equipment, **ossorio_r290_efficiencies())
# indoor DB [°C], outdoor DB [°C], net heat [W], COP = TC/IP
points = [
    (16, 3, 4227, 4227 / 1266),
    (16, 13, 4384, 4384 / 895),
    (20, 3, 4200, 4200 / 1330),
    (20, 13, 4137, 4137 / 897),
    (24, 3, 4080, 4080 / 1299),
    (24, 13, 3976, 3976 / 897),
]

if __name__ == "__main__":
    report = hp.fit(
        points,
        mode="heating",
        parameters=("overall_efficiency", "volumetric_efficiency", "heat_loss_fraction"),
        lambda_bounds=bounds,
    )
    assert report.applied, report.reason
    print(report.applied_scales, report.final_cop_rmse)
