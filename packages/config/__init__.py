"""Domain-owned configuration value objects.

The distinction this package draws is between *reading* configuration and *being*
configured. Reading it is a delivery concern: ``apps.api.config.Settings`` parses
the environment, holds the CORS list and the rate-limit budgets, and refuses to
boot on a placeholder secret. Being configured is a domain concern: the model
gateway needs an endpoint, a model name, a credential, and three bounds, and it
does not care where they came from.

While the domain read ``Settings`` directly, those two concerns were one class,
and four modules under ``services/`` imported the application to reach it — the
dependency running from the domain into delivery, which is the one direction the
architecture forbids.

So each value object here names exactly what one seam needs. They are frozen,
they have safe defaults matching the ``Settings`` defaults, and they carry no
delivery concern. ``Settings`` grows one small builder per object and stays the
only thing in the system that reads the environment.

One module today: :mod:`packages.config.providers`.
"""
