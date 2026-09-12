"""Catalogue parity harness.

Runs TMHP's *default* rules, unchanged, against published manufacturer
operating grids. A catalogue is a validation set and never a calibration set:
nothing here tunes a parameter to the unit under test. The only per-unit
inputs are values the manufacturer publishes about the machine itself --
nameplate capacity, refrigerant, and, where it is published at all,
displacement and rated air flow.
"""
