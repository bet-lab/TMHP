"""Reproducible inversion of manufacturer catalogues into model defaults.

Every number that TMHP decides on the user's behalf -- heat-exchanger
conductance, compressor displacement, rated air flow -- is derived here from
published documents rather than fitted to the validation set. Each module
writes a tracked CSV under ``validation/data/`` so the docs can cite a number
and the reader can rerun the script that produced it.

The source documents themselves live under ``validation/evidence/`` which is
deliberately untracked: publisher PDFs are not redistributable. The tracked
registry ``validation/registry/sources.yaml`` records what each file is, where
it came from and its checksum, so a reader can obtain the same document and
verify they have the same bytes.
"""
