===============
Getting Started
===============

From a fresh ``uv`` environment to a working dynamic simulation in
three short steps. Read in order — each page picks up where the
previous one left off, and the three together get you from "I just
cloned the repo" to "I'm reading per-step results out of a 24-hour
DataFrame."

.. grid:: 1 2 3 3
    :gutter: 3

    .. grid-item-card:: Installation
        :link: installation
        :link-type: doc

        Install TMHP with ``uv`` and verify the
        package imports cleanly.

    .. grid-item-card:: Quick start
        :link: quickstart
        :link-type: doc

        Run a single steady-state operating point and inspect
        the returned cycle, COP, and capacity.

    .. grid-item-card:: Your first dynamic simulation
        :link: first-dynamic-simulation
        :link-type: doc

        Drive ``analyze_dynamic`` over a 24-hour schedule and
        read the per-step results.

.. toctree::
    :maxdepth: 1
    :hidden:

    installation
    quickstart
    first-dynamic-simulation
