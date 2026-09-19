"""Keep runtime caches out of the signed application bundle."""

import sys

sys.dont_write_bytecode = True
