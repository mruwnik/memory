import os

from memory.common.modules import MODULES

# Before anything imports memory.common.settings, which reads them once at import. Every
# module named, so no test boots with module flags from a database.
os.environ["ENABLE_MODULES"] = ",".join(MODULES)
os.environ["DISABLE_MODULES"] = ""
