"""Physics-invariant suite shared by every heat-pump family (issue #96).

Grid points or defect tests that fail on the current model are marked
``xfail(strict=True, reason="#96 <ID>: ...")``. A fix PR proves itself by
removing exactly the marks that carry its defect ID.
"""
