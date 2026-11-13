========
Concepts
========

Background reading for TMHP. These pages cover *why* the library
is built the way it is — what physics-based cycle solving buys you,
how the model pieces fit together across system families, and how to
interpret the diagnostic fields each call returns.

.. grid:: 2
    :gutter: 3

    .. grid-item-card:: Why physics-based?
        :link: why-physics-based
        :link-type: doc

        Trade-offs versus empirical curve-fit models — what you give
        up, what you gain, and where the compute goes.

    .. grid-item-card:: Cycle architecture
        :link: cycle-architecture
        :link-type: doc

        How source side, refrigerant cycle, and sink side connect,
        and how the same core cycle is reused across released
        cycle-resolved families.

    .. grid-item-card:: ``failure_reason`` semantics
        :link: failure-reason-semantics
        :link-type: doc

        The four diagnostic values returned by ``analyze_steady``
        and how to branch on them.

    .. grid-item-card:: Refrigerants and CoolProp
        :link: refrigerant-and-coolprop
        :link-type: doc

        Subcritical vs supercritical operation, why CoolProp is the
        backing dependency, and how to swap refrigerants safely.

    .. grid-item-card:: Result keys
        :link: result-keys
        :link-type: doc

        Every key a result carries: meaning, unit, sign, and which
        family returns it.

    .. grid-item-card:: Scope and limits
        :link: scope-and-limits
        :link-type: doc

        What the models refuse, what they simplify, and how each
        limit shows in a result.

.. toctree::
    :maxdepth: 1
    :hidden:

    why-physics-based
    cycle-architecture
    failure-reason-semantics
    refrigerant-and-coolprop
    result-keys
    scope-and-limits
