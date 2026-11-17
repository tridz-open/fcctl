"""DEPRECATED shim: see fcctl._async.workflows.provision / fcctl._sync.workflows.provision."""
from __future__ import annotations

from .._sync.workflows.provision import (LATE, MIN_VCPU, OWNED_WINDOW, STEPS, LiveApi, ProvisionRun,  # noqa: F401
                                         is_workflow_owned, provision_site)
