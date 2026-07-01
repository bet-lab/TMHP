============
Installation
============

Requirements
============

- Python **3.10 – 3.13**
- `uv <https://docs.astral.sh/uv/>`_ package manager

Clone and install
=================

.. code-block:: bash

   git clone https://github.com/bet-lab/tmhp.git
   cd tmhp
   uv sync --locked

The ``--locked`` flag tells ``uv`` to respect the committed
``uv.lock`` and fail rather than silently re-resolve versions. This
is the same contract CI uses, so your local environment matches the
one we test against. (If you're hacking on dependencies and want to
let ``uv`` re-resolve, drop ``--locked``.)

Optional dependency groups
==========================

Development and documentation tooling are exposed as `PEP 735
<https://peps.python.org/pep-0735/>`_ dependency groups so they don't
pollute the runtime install.

.. code-block:: bash

   # Runtime only — what most users want
   uv sync --locked

   # + ruff, mypy, pytest, pytest-cov
   uv sync --group dev --locked

   # + sphinx, shibuya theme, MyST, sphinx-design, etc.
   uv sync --group docs --locked

   # Everything at once (mirrors the docs CI job)
   uv sync --all-groups --locked

Optional integration extras
===========================

The core package does not install co-simulation runtimes by default. Add the
``integrations`` extra when you need the FMI FMU adapters:

.. code-block:: bash

   uv sync --extra integrations --locked

This installs ``pythonfmu`` for FMI 2.0 export, ``pythonfmu3`` for FMI 3.0
export, and ``fmpy`` for model-description validation and smoke simulation. Both
FMU adapters wrap ``AirSourceHeatPumpBoiler.step()`` but produce separate FMI
major-version artifacts. The EnergyPlus Python Plugin adapter does not have a
pip-installable extra because ``pyenergyplus`` is bundled with EnergyPlus
itself; run that adapter inside EnergyPlus's embedded Python or make the
EnergyPlus Python package visible through ``PythonPlugin:SearchPaths``. See
:doc:`../integrations/fmu` and :doc:`../integrations/energyplus-python` for the
simulator-specific wiring.

What's installed
================

The runtime install pulls in `CoolProp <http://www.coolprop.org>`_
for refrigerant thermodynamics, NumPy / SciPy for numerical work,
pandas for per-timestep result frames, and Matplotlib for plotting,
plus a few smaller libraries for the PV and ground-loop subsystems.
The full, version-pinned list lives in ``pyproject.toml`` and
``uv.lock``.

Running the dev checks
======================

After ``uv sync --group dev --locked``, the three commands CI runs
on every PR are:

.. code-block:: bash

   uv run ruff check src/tmhp tests
   uv run mypy src/tmhp
   uv run pytest --cov=tmhp

Building the docs locally
=========================

After ``uv sync --group docs --locked``:

.. code-block:: bash

   cd docs
   uv run make html

The rendered HTML lands in ``docs/build/html``. CI builds the same
target with ``sphinx-build -W --keep-going``, so any new warning
fails the documentation job.
