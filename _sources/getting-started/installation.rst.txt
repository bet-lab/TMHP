Installation
============

Requirements
------------

- Python >= 3.10
- `uv <https://docs.astral.sh/uv/>`_ package manager

Clone & Install
---------------

**Option A — As part of** ``enex_analysis_engine`` **(recommended)**

This library is used as a git submodule of ``enex_analysis_engine``. When you clone ``enex_analysis_engine``, initialize the submodule:

.. code-block:: bash

   git clone https://github.com/bet-lab/enex_analysis_engine.git
   cd enex_analysis_engine
   git submodule update --init --recursive
   uv sync

**Option B — Standalone clone**

.. code-block:: bash

   git clone https://github.com/bet-lab/physics_hp_models.git
   cd physics_hp_models
   uv sync

Dependencies
------------

Core dependencies are automatically installed via ``uv sync``:

- **CoolProp** — refrigerant thermodynamic properties
- **NumPy / SciPy** — numerical computation
- **Pandas** — time-series data handling
- **Matplotlib** — visualization
- **pvlib** — solar irradiance calculations
- **pygfunction** — g-function borehole heat exchanger model
