"""Flask web interface (Step 3 of the build order).

Intentionally does NOT eager-import ``app`` here: doing so makes
``python -m web.app`` trigger a ``RuntimeWarning`` because runpy ends
up importing ``web.app`` twice (once via this re-export, once as
``__main__``). Callers should import explicitly::

    from web.app import create_app
"""
