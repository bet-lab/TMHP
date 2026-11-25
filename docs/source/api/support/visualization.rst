=============
Visualization
=============

Plotting facade, Mollier-style refrigerant diagrams (T-h, P-h, T-s) and the
fixed compressor-map and calibration figures.

Plotting facade
===============

.. automodule:: tmhp.visualization
    :members:
    :undoc-members:
    :show-inheritance:

Mollier diagrams
================

.. automodule:: tmhp.mollier_diagram
    :members:
    :undoc-members:
    :show-inheritance:

Compressor maps and calibration figures
=======================================

Three figures, always drawn the same way, for recording a compressor map
change or a catalogue calibration (#256). Each is drawn in the dartwork-mpl
``scientific`` preset at the width of two journal columns (17 cm), whatever
style the caller has set, and leaves the caller's ``rcParams`` as they were;
every panel has its own tick labels (#258).

* :func:`~tmhp.compressor_plots.plot_compressor_map` -- one efficiency over
  pressure ratio and speed at fixed evaporating temperatures (−15, 0 and
  10 °C by default); row 1 *before*, row 2 *after*. Every heat-pump model
  has it as a method, ``model.plot_compressor_map()``, with the model's
  efficiencies as *after* and the default maps as *before*.
* :func:`~tmhp.compressor_plots.plot_calibration_stages` -- COP RMSE and MAPE
  after each calibration stage, fitted and cross-validated.
* :func:`~tmhp.compressor_plots.plot_cop_parity` -- model against catalogue
  COP, before and after, with the error scores.

.. code-block:: python

    from tmhp import AirSourceHeatPumpBoiler

    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R290", eta_cmp_isen=my_isen)
    fig = model.plot_compressor_map()  # default maps (top) vs this model (bottom)

.. automodule:: tmhp.compressor_plots
    :members:
    :show-inheritance:
