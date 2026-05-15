Installation
============

Requirements
------------

- Python >= 3.10
- `uv <https://docs.astral.sh/uv/>`_ package manager

Clone & Install
---------------

.. code-block:: bash

   git clone https://github.com/bet-lab/physics-heatpump-models.git
   cd physics-heatpump-models
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
