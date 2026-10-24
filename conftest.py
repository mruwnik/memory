import os

from memory.common.modules import MODULES

# Before anything imports memory.common.settings, which resolves modules once at import.
os.environ["ENABLE_MODULES"] = ",".join(MODULES)
os.environ["DISABLE_MODULES"] = ""
