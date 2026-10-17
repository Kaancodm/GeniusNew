"""Start the fixed worker without executing site hooks or .pth files.

The Core checks the interpreter and these complete runtime trees before Connect.
The host installer must establish their root ownership before starting the Core.
This file is executed by absolute path with ``python -I -S``.
"""

import os
import runpy
import sys
import sysconfig


def runtime_paths() -> tuple[str, str, str]:
    return (os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            sysconfig.get_path("stdlib"), sysconfig.get_config_var("DESTSHARED"))


if __name__ == "__main__":
    # No site.addsitedir(): it executes .pth code before the worker sandbox.
    sys.path[:] = list(runtime_paths())
    runpy.run_module("geniusnew.worker_service", run_name="__main__", alter_sys=True)
