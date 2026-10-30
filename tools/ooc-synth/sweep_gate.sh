#!/usr/bin/env bash
# Sourced by sweep.sh.  Owner rule (2026-10-28): at most OOC_MAX_BUILDS (2) builds host-wide, counting chisel
# Generator, firtool and Vivado batch processes together (any user, any session).
OOC_MAX_BUILDS="${OOC_MAX_BUILDS:-2}"
OOC_VIVADO_PS_PATTERN="${OOC_VIVADO_PS_PATTERN:-[u]nwrapped/lnx64.o/vivado -mode batch}"
builds() {  # number of build processes running now
  ps -eo args | grep -E "[c]hipyard\.Generator|^[^ ]*[f]irtool |$OOC_VIVADO_PS_PATTERN" | grep -c .
}
