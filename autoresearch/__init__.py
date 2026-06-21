"""Load the project's .env at package import so DEFAULT_CONFIG's env-var overlay
(e.g. ``FRED_API_KEY``) is visible regardless of which entry point started the
process. ``find_dotenv(usecwd=True)`` walks up from the CWD, so a script run from
the repo root picks up the project's .env rather than stepping up from
site-packages. ``load_dotenv`` defaults to ``override=False``, so it never
clobbers values the caller already exported.
"""

try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except ImportError:
    pass
