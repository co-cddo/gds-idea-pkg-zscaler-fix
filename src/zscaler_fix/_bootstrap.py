"""Entry point invoked automatically via the zscaler_fix.pth file.

Do not import this module directly - use zscaler_fix.activate()/
deactivate()/is_active() instead. This module exists solely so the .pth
file has something importable to trigger activation on interpreter
startup.
"""

from zscaler_fix import activate

activate()
