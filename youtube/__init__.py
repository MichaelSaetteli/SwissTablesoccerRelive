"""YouTube Data API integration (Step 4).

Three submodules:

  * ``metadata_builder``   pure logic that turns a list of output files
                           into ``(title, description)`` pairs from the
                           templates configured in ``config.youtube``.
  * ``oauth_setup``        Google OAuth 2.0 helpers - load/save token,
                           run first-time interactive setup.
  * ``youtube_uploader``   thin wrapper around google-api-python-client
                           for video upload + playlist creation.
  * ``upload_status``      atomic JSON status persistence per discipline
                           upload (mirrors watcher.status).
"""
