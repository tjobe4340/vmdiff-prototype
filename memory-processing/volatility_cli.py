#!/usr/local/bin/python
"""Project CLI wrapper: retain upstream CLI behavior, use HTTPS for PDB retrieval."""
from volatility3.framework import constants

# Upstream 2.28.2 reads this constant when constructing symbol download URLs.
# Do this before invoking the CLI, including for diagnostics entrypoint calls.
constants.SYMBOL_SERVER_URL = "https://msdl.microsoft.com/download/symbols"

from volatility3.cli import main

if __name__ == "__main__":
    main()
